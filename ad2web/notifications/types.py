# -*- coding: utf-8 -*-
from flask import current_app
import time
import datetime
import smtplib
import threading
from email.mime.text import MIMEText
from email.utils import formatdate
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse
import sleekxmpp
import json
import re
import ssl
import sys
import base64
import uuid
import traceback
import functools
from alarmdecoder import AlarmDecoder
from alarmdecoder.panels import ADEMCO, DSC, PANEL_TYPES
from alarmdecoder.zonetracking import Zone as ADZone
try:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    have_threadpoolexecutor = True
except ImportError:
    have_threadpoolexecutor = False
try:
    from chump import Application
    have_chump = True
except ImportError:
    have_chump = False
try:
    import twilio
    try:
        from twilio.rest import TwilioRestClient
        from twilio.TwilioRestException import TwilioRestException
        have_twilio = True
    except ImportError:
        try:
            from twilio.rest import Client as TwilioRestClient
            from twilio.base.exceptions import TwilioRestException
            have_twilio = True
        except ImportError:
            have_twilio = False
except ImportError:
    have_twilio = False
from xml.dom.minidom import parseString
from xml.etree.ElementTree import Element, SubElement, Comment, tostring
import ast
try:
    from http.client import HTTPSConnection
except ImportError:
    from httplib import HTTPSConnection
try:
    from http.client import HTTPConnection
except ImportError:
    from httplib import HTTPConnection
try:
    from urllib.parse import urlencode, quote
except ImportError:
    from urllib import urlencode, quote
import logging
try:
    import gntp.notifier
    have_gntp = True
except ImportError:
    have_gntp = False
from .constants import (
    EMAIL, DEFAULT_EVENT_MESSAGES, PUSHOVER, TWILIO, PROWL, PROWL_URL, PROWL_PATH,
    PROWL_EVENT, PROWL_METHOD, PROWL_CONTENT_TYPE, PROWL_HEADER_CONTENT_TYPE,
    PROWL_USER_AGENT, GROWL_APP_NAME, GROWL_DEFAULT_NOTIFICATIONS, GROWL_PRIORITIES,
    GROWL, CUSTOM, URLENCODE, JSON, XML, CUSTOM_CONTENT_TYPES, CUSTOM_USER_AGENT,
    CUSTOM_METHOD, CUSTOM_METHOD_GET, CUSTOM_METHOD_POST, CUSTOM_METHOD_GET_TYPE,
    CUSTOM_TIMESTAMP, CUSTOM_MESSAGE, CUSTOM_REPLACER_SEARCH, TWIML, ARM, DISARM,
    ALARM, PANIC, FIRE, MATRIX, UPNPPUSH, LRR, READY, CHIME, TIME_MULTIPLIER,
    XML_EVENT_TEMPLATE, XML_EVENT_PROPERTY, EVENT_TYPES, RAW_MESSAGE, EVENTID_MESSAGE,
    EVENTDESC_MESSAGE, POWER_CHANGED, BOOT, LOW_BATTERY, RFX, EXP, AUI, BYPASS,
    ZONE_FAULT, ZONE_RESTORE
)
from .models import Notification, NotificationSetting, NotificationMessage
from ..extensions import db
from ..log.models import EventLogEntry
from ..settings import Setting
from ..zones import Zone
from ..utils import user_is_authenticated

# Inline time restriction logic (replaces NotificationSetting.check_time_restriction)
def check_time_restriction(start_time, end_time):
    st = start_time.split(':')
    et = end_time.split(':')
    message_time = datetime.datetime.now()
    start_dt = message_time.replace(hour=int(st[0]), minute=int(st[1]), second=int(st[2]), microsecond=0)
    end_dt = message_time.replace(hour=int(et[0]), minute=int(et[1]), second=int(et[2]), microsecond=0)
    # Adjust dates if time range spans midnight
    if end_dt.hour < start_dt.hour:
        if message_time.hour < end_dt.hour:
            start_dt -= datetime.timedelta(days=1)
        else:
            end_dt += datetime.timedelta(days=1)
    return start_dt <= message_time <= end_dt

# Decorator for better logging of notification task exceptions.
def raise_with_stack(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            tb = traceback.format_exc().splitlines()
            raise Exception("%s %s" % (repr(e), tb[3].split(",")[1].strip() if len(tb) > 3 else ""))
    return wrapped

# Decorator for executing functions in a thread pool (for IO-heavy tasks).
def threaded(func):
    def wrapper(*args, **kwargs):
        if have_threadpoolexecutor:
            notifier_system = current_app.decoder._notifier_system
            app = current_app._get_current_object()
            future = notifier_system._tpool.submit(func, *args, app=app, **kwargs)
            future.fcname = "%s.%s()" % (args[0].__class__.__name__, func.__name__)
            with notifier_system._lock:
                notifier_system._futures.append(future)
        else:
            return func(*args, **kwargs)
    return wrapper

class NotificationSystem(object):
    def __init__(self):
        self._notifiers = {}
        self._messages = DEFAULT_EVENT_MESSAGES
        self._wait_list = []
        self._tpool = None
        self._lock = threading.Lock()
        self._futures = []
        self._init_notifiers()
        # Subscribers for UPNP push notifications
        self._subscribers = {}
        # Initialize ThreadPoolExecutor if available
        if have_threadpoolexecutor:
            current_app.logger.info('Library concurrent.futures.ThreadPoolExecutor loaded.')
            workers = Setting.get_by_name('max_notification_workers', default=5).value
            if workers:
                current_app.logger.info('ThreadPoolExecutor enabled for notifications with max_workers {}.'.format(workers))
                self._tpool = ThreadPoolExecutor(max_workers=int(workers))
            else:
                current_app.logger.info('ThreadPoolExecutor for notifications disabled.')
        else:
            current_app.logger.info('Library concurrent.futures.ThreadPoolExecutor not found. Threaded notifications disabled.')

    def send(self, type, **kwargs):
        errors = []
        for id, n in self._notifiers.items():
            if n and n.subscribes_to(type, **kwargs):
                try:
                    message, rawmessage = self._build_message(type, **kwargs)
                    if message:
                        if n.delay and n.delay > 0 and type in (ZONE_FAULT, ZONE_RESTORE, BYPASS):
                            message_send_time = (datetime.datetime.now() + datetime.timedelta(minutes=n.delay)).timestamp()
                            notify = {
                                'notification': n,
                                'message_send_time': message_send_time,
                                'message': message,
                                'raw': rawmessage,
                                'type': type,
                                'zone': int(kwargs.get('zone', -1))
                            }
                            if notify not in self._wait_list:
                                self._wait_list.append(notify)
                        else:
                            n.send(type, message, rawmessage)
                except Exception as err:
                    errors.append('Exception in notification {}.send(): {}'.format(n.__class__.__name__, str(err)))
        return errors

    def refresh_notifier(self, id):
        n = Notification.query.filter_by(id=id, enabled=1).first()
        if n:
            self._notifiers[id] = TYPE_MAP[n.type](n)
        else:
            try:
                del self._notifiers[id]
            except KeyError:
                pass

    def test_notifier(self, id):
        try:
            n = self._notifiers.get(id)
            if n:
                n.send(None, 'Test Notification', None)
        except Exception as err:
            return str(err)
        else:
            return None

    def add_subscriber(self, host, callback, timeout):
        """
        Add a subscriber callback to our dictionary (for UPNP push notifications).
        """
        sub_uuid = None
        try:
            # If this host+callback is already subscribed, reuse the same subscription ID.
            for k, v in self._subscribers.items():
                if v['host'] == host and v['callback'] == callback:
                    sub_uuid = k
                    break
            if sub_uuid is None:
                sub_uuid = str(uuid.uuid1())
            # Calculate expiration time
            tmultiplier, tval = timeout.split("-", 1)
            tlength = TIME_MULTIPLIER.get(tmultiplier, 1) * int(tval)
            self._subscribers[sub_uuid] = {'host': host, 'callback': callback, 'expire': time.time() + tlength}
            current_app.logger.info('add_subscriber: {}'.format(sub_uuid))
        except Exception as err:
            current_app.logger.error('Error adding subscriber for host:{} callback:{} timeout:{} err: {}'.format(host, callback, timeout, str(err)))
            return None
        return sub_uuid

    def remove_subscriber(self, host, subuuid):
        """
        Remove a subscriber if found in our dictionary.
        """
        found = self._subscribers.pop(subuuid, None)
        if found:
            current_app.logger.info('remove_subscriber: found {}'.format(subuuid))
        else:
            current_app.logger.info('remove_subscriber: not found {}'.format(subuuid))

    def get_subscribers(self):
        return self._subscribers

    def _init_notifiers(self):
        # Always include LogNotification
        self._notifiers = {-1: LogNotification()}
        for n in Notification.query.filter_by(enabled=1).all():
            self._notifiers[n.id] = TYPE_MAP[n.type](n)

    def _build_message(self, type, **kwargs):
        message = NotificationMessage.query.filter_by(id=type).first()
        if message:
            message = message.text
        kwargs = self._fill_replacers(type, **kwargs)
        if message:
            message = message.format(**kwargs)
        rawmessage = kwargs.get('message', None)
        if rawmessage:
            rawmessage = getattr(rawmessage, 'raw', None)
        return message, rawmessage

    def _fill_replacers(self, type, **kwargs):
        if 'zone' in kwargs:
            zone_name = Zone.get_name(kwargs['zone'])
            kwargs['zone_name'] = zone_name if zone_name else '<unnamed>'
        if type == ARM:
            status = kwargs.get('stay', False)
            kwargs['arm_type'] = 'STAY' if status else 'AWAY'
        if type == LRR:
            message = kwargs.get('message', None)
            if hasattr(message, "dict"):
                message = message.dict()
                vp = message.get('partition', -1)
                ved = message.get('event_description', 'Unknown')
                vs = 'Event' if message.get('event_status', 1) == 1 else 'Restore'
                vedt = message.get('event_data_type', -1)
                vd = message.get('event_data', -1)
                kwargs['status'] = "Partition {0} {1} {2} {3}{4}".format(vp, ved, vs, vedt, vd)
            else:
                kwargs['status'] = message
        if type == AUI:
            message = kwargs.get('message', None)
            if hasattr(message, "dict"):
                message = message.dict()
                kwargs['value'] = message.get('value')
        if type == EXP:
            message = kwargs.get('message', None)
            if hasattr(message, "dict"):
                expmessage = message.dict()
                kwargs['type'] = "ZONE" if message.type == 0 else "RELAY"
                kwargs['address'] = expmessage.get('address')
                kwargs['channel'] = expmessage.get('channel')
                kwargs['value'] = expmessage.get('value')
        if type == RFX:
            message = kwargs.get('message', None)
            if hasattr(message, "dict"):
                rfxmessage = message.dict()
                kwargs['sn'] = rfxmessage.get('serial_number')
                kwargs['bat'] = int(rfxmessage.get('battery')) if rfxmessage.get('battery') is not None else None
                kwargs['supv'] = int(rfxmessage.get('supervision')) if rfxmessage.get('supervision') is not None else None
        return kwargs

    def process_wait_list(self):
        errors = []
        # First, remove queued events if a suppression condition is met
        for notifier in list(self._wait_list):
            try:
                if notifier['notification'].suppress and notifier['notification'].suppress != 0:
                    if self._check_suppress(notifier):
                        self._remove_suppressed_zone(notifier['zone'])
            except Exception as err:
                errors.append('Error processing suppression for {}: {}'.format(notifier['notification'].description, str(err)))
        # Then send any queued events whose delay time has passed
        for notifier in list(self._wait_list):
            try:
                if time.time() >= notifier['message_send_time']:
                    notifier['notification'].send(notifier['type'], notifier['message'], notifier['raw'])
                    self._wait_list.remove(notifier)
            except Exception as err:
                errors.append('Error sending notification for {}: {}'.format(notifier['notification'].description, str(err)))
        return errors

    def _check_suppress(self, notifier):
        if notifier['type'] in (ZONE_RESTORE, BYPASS):
            zone = notifier['zone']
            for n in self._wait_list:
                if n['zone'] == zone:
                    if n['type'] == ZONE_FAULT and getattr(n['notification'], 'suppress', 0):
                        return True
        return False

    def _remove_suppressed_zone(self, zone_id):
        to_remove = [n for n in self._wait_list if n['zone'] != -1 and n['zone'] == zone_id]
        for n in to_remove:
            try:
                self._wait_list.remove(n)
            except ValueError:
                pass

class NotificationThread(threading.Thread):
    def __init__(self, decoder):
        threading.Thread.__init__(self)
        self._decoder = decoder
        self._running = False

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        notifier = self._decoder._notifier_system
        while self._running:
            with notifier._lock:
                ncount = len(notifier._futures)
                if ncount > 0:
                    with self._decoder.app.app_context():
                        current_app.logger.info('Background notification functions running {}.'.format(ncount))
                    remove = []
                    for f in list(notifier._futures):
                        if f.done():
                            extra_msg = ""
                            try:
                                _ = f.result()
                            except Exception as exc:
                                extra_msg = exc
                            else:
                                extra_msg = 'no exceptions'
                            with self._decoder.app.app_context():
                                current_app.logger.info('Background notification function {} finished with {}.'.format(getattr(f, 'fcname', '<unknown>'), extra_msg))
                            remove.append(f)
                    for f in remove:
                        try:
                            notifier._futures.remove(f)
                        except ValueError:
                            pass
            # Process delayed notifications outside the lock
            with self._decoder.app.app_context():
                errors = notifier.process_wait_list()
                for e in errors:
                    current_app.logger.error(e)
            time.sleep(5)

class BaseNotification(object):
    def __init__(self, obj):
        self._subscriptions = {int(k): v for k, v in json.loads(obj.settings['subscriptions'].value).items()} if 'subscriptions' in obj.settings.keys() else {}
        self._zone_filters = [int(k) for k in json.loads(obj.settings['zone_filter'].value)] if 'zone_filter' in obj.settings.keys() else []
        self.id = obj.id
        self.description = obj.description
        self.starttime = obj.get_setting('starttime', default='00:00:00')
        self.endtime = obj.get_setting('endtime', default='23:59:59')
        self.delay = obj.get_setting('delay', default=0)
        if self.delay is None or self.delay == '':
            self.delay = 0
        self.suppress = obj.get_setting('suppress', default=True)

    def subscribes_to(self, type, **kwargs):
        if type in self._subscriptions.keys():
            if type in (ZONE_FAULT, ZONE_RESTORE, BYPASS):
                zone = kwargs.get('zone', -1)
                if int(zone if zone else -1) in self._zone_filters:
                    return True
                else:
                    return False
            return True
        return False

class LogNotification(object):
    def __init__(self):
        self.id = -1
        self.description = 'Logger'
        self.delay = 0
        self.suppress = 0

    def subscribes_to(self, type, **kwargs):
        return True

    def send(self, type, text, raw):
        with current_app.app_context():
            if type in (ZONE_RESTORE, ZONE_FAULT, BYPASS):
                current_app.logger.debug('Event: {}'.format(text))
            else:
                current_app.logger.info('Event: {}'.format(text))
        db.session.add(EventLogEntry(type=type, message=text))
        db.session.commit()

class UPNPPushNotification(BaseNotification):
    def __init__(self, obj):
        BaseNotification.__init__(self, obj)
        self.notification_description = obj.description
        # Events that will trigger UPNP push notifications (not user-configurable)
        self._events = [LRR, RFX, EXP, AUI, READY, CHIME, ARM, DISARM, ALARM, PANIC, FIRE, BYPASS, ZONE_FAULT, ZONE_RESTORE, BOOT, POWER_CHANGED, LOW_BATTERY]
        self.description = 'UPNPPush'

    def subscribes_to(self, type, **kwargs):
        return (type in self._events)

    @raise_with_stack
    def send(self, type, text, raw):
        # Send to all UPNP subscribers (type None is used for test notifications)
        if type is None or type in self._events:
            self._notify_subscribers(type, text, raw)

    def _notify_subscribers(self, type, text, raw):
        panel_state = self._build_panel_state()
        event_type_desc = EVENT_TYPES[type] if type is not None else "Testing"
        response = XML_EVENT_TEMPLATE.format(
            self._build_property("eventid", type, False),
            self._build_property("eventdesc", event_type_desc, False),
            self._build_property("eventmessage", text, True),
            self._build_property("rawmessage", raw if raw is not None else "", True),
            panel_state
        )
        subscribers = current_app.decoder._notifier_system.get_subscribers()
        for sid, info in subscribers.items():
            try:
                self._send_notify_event(sid, info['callback'], response)
            except Exception as err:
                current_app.logger.error("Failed to notify subscriber {}: {}".format(sid, err))

    def _build_property(self, name, value, cdatatag):
        if value is not None:
            if cdatatag:
                xml_val = "<![CDATA[{}]]>".format(value)
            else:
                xml_val = str(value)
            return XML_EVENT_PROPERTY.format(name, xml_val)
        return ""

    def _build_panel_state(self):
        mode = current_app.decoder.device.mode
        if mode == ADEMCO:
            mode_str = "ADEMCO"
        elif mode == DSC:
            mode_str = "DSC"
        else:
            mode_str = "UNKNOWN"
        panel_state_elem = Element("panel_state")
        mode_elem = Element("panel_mode")
        mode_elem.text = mode_str
        panel_state_elem.append(mode_elem)
        relay_status_elem = Element("panel_relay_status")
        try:
            relay_items = current_app.decoder.device._relay_status.items()
        except AttributeError:
            relay_items = []
        for (address, channel), value in relay_items:
            child = Element("r")
            child.set("address", str(address))
            child.set("channel", str(channel))
            child.text = str(value)
            relay_status_elem.append(child)
        panel_state_elem.append(relay_status_elem)
        return tostring(panel_state_elem).decode('utf-8')

    def _send_notify_event(self, sid, callback_url, response):
        url = urlparse(callback_url)
        conn = None
        try:
            if url.scheme.lower() == "https":
                conn = HTTPSConnection(url.hostname, url.port or 443, context=ssl._create_unverified_context())
            else:
                conn = HTTPConnection(url.hostname, url.port or 80)
            headers = {"Content-Type": "text/xml"}
            conn.request("NOTIFY", url.path or "/", body=response, headers=headers)
            http_response = conn.getresponse()
            current_app.logger.info('{} _send_notify_event: status:{} reason:{} headers:{}'.format(self.description, http_response.status, http_response.reason, dict(http_response.getheaders())))
            if http_response.status not in (200, 204):
                error_msg = '{} Notification failed: ({}: {})'.format(self.description, http_response.status, http_response.reason)
                current_app.logger.warning(error_msg)
                raise Exception(error_msg)
        finally:
            if conn:
                conn.close()

class MatrixNotification(BaseNotification):
    def __init__(self, obj):
        BaseNotification.__init__(self, obj)
        self.notification_description = obj.description
        self._events = [LRR, EXP, AUI, READY, CHIME, ARM, DISARM, ALARM, PANIC, FIRE, BYPASS, ZONE_FAULT, ZONE_RESTORE, BOOT]
        self.api_endpoint = obj.get_setting('domain')
        self.api_token = obj.get_setting('token')
        self.api_room_id = obj.get_setting('room_id')
        self.custom_values = obj.get_setting('custom_values')
        self.headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Content-type': CUSTOM_CONTENT_TYPES[JSON]
        }

    @raise_with_stack
    def send(self, type, text, raw):
        result = False
        try:
            if check_time_restriction(self.starttime, self.endtime):
                message_obj = NotificationMessage.query.filter_by(id=type).first()
                if message_obj:
                    _ = message_obj.text  # The message text is not directly used in Matrix send
                notify_data = {
                    'msgtype': 'm.text',
                    'body': "From {}: {}".format(self.notification_description, text),
                    'notifier': self.notification_description,
                    'eventid': type,
                    'eventdesc': (EVENT_TYPES[type] if type is not None else "Testing"),
                    'raw': raw
                }
                if self.custom_values:
                    try:
                        self.custom_values = ast.literal_eval(self.custom_values)
                    except ValueError:
                        pass
                    if isinstance(self.custom_values, list):
                        for cv in self.custom_values:
                            notify_data[str(cv.get('custom_key'))] = cv.get('custom_value')
                for key, val in notify_data.items():
                    if val == CUSTOM_REPLACER_SEARCH.get(CUSTOM_TIMESTAMP):
                        notify_data[key] = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(time.time()))
                    if val == CUSTOM_REPLACER_SEARCH.get(CUSTOM_MESSAGE):
                        notify_data[key] = text
                    if val == CUSTOM_REPLACER_SEARCH.get(RAW_MESSAGE):
                        notify_data[key] = raw or ""
                    if val == CUSTOM_REPLACER_SEARCH.get(EVENTID_MESSAGE):
                        notify_data[key] = type
                    if val == CUSTOM_REPLACER_SEARCH.get(EVENTDESC_MESSAGE):
                        notify_data[key] = EVENT_TYPES.get(type, "")
                result = self._do_post(self._dict_to_json(notify_data))
        except Exception as e:
            raise Exception('Matrix Notification Failed: {} line: {}'.format(e, sys.exc_info()[-1].tb_lineno))
        return result

    @threaded
    @raise_with_stack
    def _do_post(self, data, app=None):
        if app is None:
            app = current_app
        conn = None
        try:
            conn = HTTPSConnection(self.api_endpoint, context=ssl._create_unverified_context())
            path = "/_matrix/client/r0/rooms/{}/send/m.room.message?access_token={}".format(self.api_room_id, self.api_token)
            conn.request("POST", path, headers=self.headers, body=data)
            http_response = conn.getresponse()
            if http_response.status < 200 or http_response.status >= 300:
                error_msg = "{} Notification failed: ({}: {})".format(self.description, http_response.status, http_response.reason)
                app.logger.warning(error_msg)
                raise Exception(error_msg)
            return True
        finally:
            if conn:
                conn.close()

    def _dict_to_json(self, d):
        return json.dumps(d)

class EmailNotification(BaseNotification):
    def __init__(self, obj):
        BaseNotification.__init__(self, obj)
        self.notification_description = obj.description
        self.source = obj.get_setting('source')
        self.destination = obj.get_setting('destination')
        self.subject = obj.get_setting('subject')
        self.server = obj.get_setting('server')
        self.port = obj.get_setting('port', default=25)
        self.tls = obj.get_setting('tls', default=False)
        self.ssl = obj.get_setting('ssl', default=False)
        self.authentication_required = obj.get_setting('authentication_required', default=False)
        self.username = obj.get_setting('username')
        self.password = obj.get_setting('password')
        self.suppress_timestamp = obj.get_setting('suppress_timestamp', default=False)

    @raise_with_stack
    def send(self, type, text, raw):
        if check_time_restriction(self.starttime, self.endtime):
            msg = MIMEText(text)
            if not self.suppress_timestamp:
                message_timestamp = time.ctime(time.time())
                msg['Subject'] = "{} ({})".format(self.subject, message_timestamp)
            else:
                msg['Subject'] = self.subject
            msg['From'] = self.source
            recipients = re.split(r'\s*;\s*|\s*,\s*', self.destination)
            msg['To'] = ', '.join(recipients)
            msg['Date'] = formatdate(localtime=True)
            self._send(recipients, msg)

    @threaded
    @raise_with_stack
    def _send(self, recipients, msg, app=None):
        if app is None:
            app = current_app
        s = smtplib.SMTP_SSL(self.server, self.port) if self.ssl else smtplib.SMTP(self.server, self.port)
        if self.tls and not self.ssl:
            s.starttls()
        if self.authentication_required:
            s.login(str(self.username), str(self.password))
        s.sendmail(self.source, recipients, msg.as_string())
        s.quit()

class PushoverNotification(BaseNotification):
    def __init__(self, obj):
        BaseNotification.__init__(self, obj)
        self.notification_description = obj.description
        self.token = obj.get_setting('token')
        self.user_key = obj.get_setting('user_key')
        self.priority = obj.get_setting('priority')
        self.title = obj.get_setting('title')

    @raise_with_stack
    def send(self, type, text, raw):
        if not have_chump:
            raise Exception('Missing Pushover library: chump - install using pip')
        if check_time_restriction(self.starttime, self.endtime):
            app = Application(self.token)
            if app.is_authenticated:
                user = app.get_user(self.user_key)
                if user_is_authenticated(user):
                    message = user.create_message(
                        title=self.title,
                        message=text,
                        html=True,
                        priority=self.priority,
                        timestamp=int(time.time())
                    )
                    is_sent = message.send()
                    if is_sent is not True:
                        current_app.logger.info("Pushover Notification Failed")
                        raise Exception('Pushover Notification Failed')
                else:
                    current_app.logger.info("Pushover Notification Failed - bad user key: " + self.user_key)
                    raise Exception("Pushover Notification Failed - bad user key: " + self.user_key)
            else:
                current_app.logger.info("Pushover Notification Failed - bad application token: " + self.token)
                raise Exception("Pushover Notification Failed - bad application token: " + self.token)

class TwilioNotification(BaseNotification):
    def __init__(self, obj):
        BaseNotification.__init__(self, obj)
        self.notification_description = obj.description
        self.account_sid = obj.get_setting('account_sid')
        self.auth_token = obj.get_setting('auth_token')
        self.number_to = obj.get_setting('number_to')
        self.number_from = obj.get_setting('number_from')
        self.suppress_timestamp = obj.get_setting('suppress_timestamp', default=False)

    @raise_with_stack
    def send(self, type, text, raw):
        if not have_twilio:
            raise Exception('Missing Twilio library: twilio - install using pip')
        text = " From " + self.notification_description + ". " + text
        if check_time_restriction(self.starttime, self.endtime):
            if not self.suppress_timestamp:
                message_timestamp = time.ctime(time.time())
                msg_to_send = text + " Message Sent at: " + message_timestamp
            else:
                msg_to_send = text
            self._send(msg_to_send)

    @threaded
    @raise_with_stack
    def _send(self, twbody, app=None):
        if app is None:
            app = current_app
        try:
            client = TwilioRestClient(self.account_sid, self.auth_token)
            client.messages.create(to=self.number_to, from_=self.number_from, body=twbody)
        except TwilioRestException as e:
            app.logger.info('Event Twilio Notification Failed: {}'.format(e))
            raise Exception('Twilio Notification Failed: {}'.format(e))

class TwiMLNotification(BaseNotification):
    def __init__(self, obj):
        BaseNotification.__init__(self, obj)
        self.notification_description = obj.description
        self.account_sid = obj.get_setting('account_sid')
        self.auth_token = obj.get_setting('auth_token')
        self.number_to = obj.get_setting('number_to')
        self.number_from = obj.get_setting('number_from')
        self.url = obj.get_setting('twimlet_url')
        self.suppress_timestamp = obj.get_setting('suppress_timestamp', default=False)

    @raise_with_stack
    def send(self, type, text, raw):
        text = " From " + self.notification_description + ". " + text
        if check_time_restriction(self.starttime, self.endtime):
            if not self.suppress_timestamp:
                message_timestamp = time.ctime(time.time())
                self.msg_to_send = text + " Message Sent at: " + message_timestamp + "."
            else:
                self.msg_to_send = text
            if not have_twilio:
                raise Exception('Missing Twilio library: twilio - install using pip')
            notify_url = self.url + "?" + quote("Message[0]") + "=" + quote(self.msg_to_send)
            self._send(notify_url)

    @threaded
    @raise_with_stack
    def _send(self, twurl, app=None):
        if app is None:
            app = current_app
        try:
            client = TwilioRestClient(self.account_sid, self.auth_token)
            client.calls.create(to="+" + self.number_to, from_="+" + self.number_from, url=twurl)
        except TwilioRestException as e:
            app.logger.info('Event TwiML Notification Failed: {}'.format(e))
            raise Exception('TwiML Notification Failed: {}'.format(e))

class ProwlNotification(BaseNotification):
    def __init__(self, obj):
        BaseNotification.__init__(self, obj)
        self.notification_description = obj.description
        self.api_key = obj.get_setting('prowl_api_key')
        app_name = obj.get_setting('prowl_app_name') or ""
        self.app_name = app_name[:256]
        self.priority = obj.get_setting('prowl_priority')
        self.event = PROWL_EVENT[:1024]
        self.content_type = PROWL_CONTENT_TYPE
        self.headers = {
            'User-Agent': PROWL_USER_AGENT,
            'Content-type': PROWL_HEADER_CONTENT_TYPE
        }
        self.suppress_timestamp = obj.get_setting('suppress_timestamp', default=False)

    @raise_with_stack
    def send(self, type, text, raw):
        if check_time_restriction(self.starttime, self.endtime):
            if not self.suppress_timestamp:
                message_timestamp = time.ctime(time.time())
                self.msg_to_send = text[:10000] + " Message Sent at: " + message_timestamp
            else:
                self.msg_to_send = text[:10000]
            notify_data = {
                'apikey': self.api_key,
                'application': self.app_name,
                'event': self.event,
                'description': self.msg_to_send,
                'priority': self.priority
            }
            # Include origin description in plain text message
            self.msg_to_send = text + " From " + self.notification_description + "."
            conn = HTTPSConnection(PROWL_URL, context=ssl._create_unverified_context()) if hasattr(ssl, '_create_unverified_context') else HTTPSConnection(PROWL_URL)
            conn.request(PROWL_METHOD, PROWL_PATH, headers=self.headers, body=urlencode(notify_data))
            http_response = conn.getresponse()
            if http_response.status != 200:
                current_app.logger.info('Event Prowl Notification Failed: {}'.format(http_response.reason))
                raise Exception('Prowl Notification Failed: {}'.format(http_response.reason))
            conn.close()

class GrowlNotification(BaseNotification):
    def __init__(self, obj):
        BaseNotification.__init__(self, obj)
        self.notification_description = obj.description
        self.priority = obj.get_setting('growl_priority')
        self.hostname = obj.get_setting('growl_hostname')
        self.port = obj.get_setting('growl_port')
        self.password = obj.get_setting('growl_password')
        if self.password == '':
            self.password = None
        self.title = obj.get_setting('growl_title')
        if have_gntp:
            self.growl = gntp.notifier.GrowlNotifier(
                applicationName=GROWL_APP_NAME,
                notifications=GROWL_DEFAULT_NOTIFICATIONS,
                defaultNotifications=GROWL_DEFAULT_NOTIFICATIONS,
                hostname=self.hostname,
                password=self.password
            )
        else:
            self.growl = None
        self.suppress_timestamp = obj.get_setting('suppress_timestamp', default=False)

    @raise_with_stack
    def send(self, type, text, raw):
        if not have_gntp:
            raise Exception('Missing Growl library: gntp - install using pip')
        if check_time_restriction(self.starttime, self.endtime):
            if not self.suppress_timestamp:
                message_timestamp = time.ctime(time.time())
                self.msg_to_send = text + " Message Sent at: " + message_timestamp
            else:
                self.msg_to_send = text
            if self.growl:
                growl_status = self.growl.register()
                if growl_status is True:
                    result = self.growl.notify(
                        noteType=GROWL_DEFAULT_NOTIFICATIONS[0],
                        title=self.title,
                        description=self.msg_to_send,
                        priority=self.priority,
                        sticky=False
                    )
                    if result is not True:
                        current_app.logger.info('Event Growl Notification Failed: {}'.format(result))
                        raise Exception('Growl Notification Failed: {}'.format(result))
                else:
                    current_app.logger.info('Event Growl Notification Failed: {}'.format(growl_status))
                    raise Exception('Growl Notification Failed: {}'.format(growl_status))

class CustomNotification(BaseNotification):
    def __init__(self, obj):
        BaseNotification.__init__(self, obj)
        self.notification_description = obj.description
        self.url = obj.get_setting('custom_url')
        self.path = obj.get_setting('custom_path')
        self.is_ssl = obj.get_setting('is_ssl')
        self.post_type = obj.get_setting('post_type')
        self.require_auth = obj.get_setting('require_auth')
        self.auth_username = obj.get_setting('auth_username', default='').replace('\n', '')
        self.auth_password = obj.get_setting('auth_password', default='').replace('\n', '')
        self.custom_values = obj.get_setting('custom_values')
        self.content_type = CUSTOM_CONTENT_TYPES[self.post_type]
        self.method = obj.get_setting('method')
        self.headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Content-type': self.content_type
        }
        if self.require_auth:
            auth_string = self.auth_username + ':' + self.auth_password
            auth_bytes = base64.b64encode(auth_string.encode('utf-8'))
            self.headers['Authorization'] = "Basic " + auth_bytes.decode('utf-8')

    def _dict_to_xml(self, tag, d):
        el = Element(tag)
        for key, val in d.items():
            child = Element(str(key))
            child.text = str(val)
            el.append(child)
        return tostring(el)

    def _dict_to_json(self, d):
        return json.dumps(d)

    @threaded
    @raise_with_stack
    def _do_post(self, data, app=None):
        if app is None:
            app = current_app
        conn = HTTPSConnection(self.url, context=ssl._create_unverified_context()) if self.is_ssl else HTTPConnection(self.url)
        conn.request(CUSTOM_METHOD_POST, self.path, body=data, headers=self.headers)
        http_response = conn.getresponse()
        if 200 <= http_response.status <= 299:
            return True
        else:
            app.logger.warning('Custom Notification Failed on POST method ({}: {})'.format(http_response.status, http_response.reason))
            raise Exception('Custom Notification Failed')
        # conn closed by garbage collection or at program exit

    @raise_with_stack
    def send(self, type, text, raw):
        self.msg_to_send = text
        result = False
        if check_time_restriction(self.starttime, self.endtime):
            notify_data = {}
            if self.custom_values:
                try:
                    self.custom_values = ast.literal_eval(self.custom_values)
                except ValueError:
                    pass
                if isinstance(self.custom_values, list) and len(self.custom_values) > 0:
                    notify_data = {str(i['custom_key']): i['custom_value'] for i in self.custom_values}
            if notify_data:
                for key, val in list(notify_data.items()):
                    if val == CUSTOM_REPLACER_SEARCH.get(CUSTOM_TIMESTAMP):
                        notify_data[key] = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(time.time()))
                    if val == CUSTOM_REPLACER_SEARCH.get(CUSTOM_MESSAGE):
                        notify_data[key] = self.msg_to_send
                    if val == CUSTOM_REPLACER_SEARCH.get(RAW_MESSAGE):
                        notify_data[key] = raw or ""
                    if val == CUSTOM_REPLACER_SEARCH.get(EVENTID_MESSAGE):
                        notify_data[key] = type
                    if val == CUSTOM_REPLACER_SEARCH.get(EVENTDESC_MESSAGE):
                        notify_data[key] = EVENT_TYPES.get(type, "")
            if self.method == CUSTOM_METHOD_POST:
                if self.post_type == URLENCODE:
                    result = self._do_post(urlencode(notify_data))
                elif self.post_type == XML:
                    result = self._do_post(self._dict_to_xml('notification', notify_data))
                elif self.post_type == JSON:
                    result = self._do_post(self._dict_to_json(notify_data))
            elif self.method == CUSTOM_METHOD_GET_TYPE:
                if self.post_type == URLENCODE:
                    conn = HTTPSConnection(self.url, context=ssl._create_unverified_context()) if self.is_ssl else HTTPConnection(self.url)
                    get_path = self.path + '?' + urlencode(notify_data)
                    conn.request(CUSTOM_METHOD_GET, get_path, headers=self.headers)
                    http_response = conn.getresponse()
                    if 200 <= http_response.status <= 299:
                        result = True
                    else:
                        current_app.logger.warning('Custom Notification Failed on GET method ({}: {})'.format(http_response.status, http_response.reason))
                        raise Exception('Custom Notification Failed')
                    conn.close()
                else:
                    result = False
        return result

TYPE_MAP = {
    EMAIL: EmailNotification,
    PUSHOVER: PushoverNotification,
    TWILIO: TwilioNotification,
    PROWL: ProwlNotification,
    GROWL: GrowlNotification,
    CUSTOM: CustomNotification,
    TWIML: TwiMLNotification,
    UPNPPUSH: UPNPPushNotification,
    MATRIX: MatrixNotification
}
