{% include 'js/setup/enrollment.js' %}
<script type="text/javascript">
    $(document).ready(function() {
        $.fn.spin.presets.flower = {
            lines: 13,
            length: 30,
            width: 10,
            radius: 30,
            className: 'spinner'
        }
        // Show loading spinner until DataTable is ready
        $('#loading').spin('flower');
        $('#zones-table').DataTable({
            responsive: true,
            stateSave: true,
            stateDuration: 60 * 60 * 24,
            pagingType: "full_numbers",
            language: {
                infoEmpty: "No Results",
                infoFiltered: "",
                emptyTable: " ",
                info: "_START_ to _END_ of _TOTAL_"
            },
            order: [[1, "asc"]],
            initComplete: function() {
                // Hide spinner and display table once loaded
                $('#loading').stop();
                $('#loading').hide();
                $('#clear').css('display', 'inline-block');
                $('#datatable').show();
            }
        });
        // Subscribe to panel messages (AUI) for zone scanning
        PubSub.subscribe('message', function(type, msg) {
            if (msg.message_type == "aui") {
                prefix = getAUIPrefix(msg.value);
                value = msg.value.trim();
                if (state == states['getPartitionCount']) {
                    partitionCount = parseAUIMessage(prefix, value);
                    state = states['getPartitionCountDone'];
                    decoder.emit('keypress', "K18\r\n");
                }
                if (state == states['getZoneData']) {
                    zone = parseAUIMessage(prefix, value);
                }
            }
            if (state == states['getZoneDataDone']) {
                if (zones.length > 0) {
                    populateZones(zones);
                }
            }
        });
        // If panel mode is 0 (ADEMCO), set configuration bits (for scanning)
        if ({{ panel_mode }} == 0) {
            setConfigBits();
        }
        // Handler for "Scan Panel" button click: confirm and start scanning
        $('#importZone').on('click', function() {
            $.confirm({
                content: "This will take a few minutes - this will also delete existing configured zones from the WebApp.<br/>This is not compatible with SE panels or panels without AUI support. You do not need an AUI keypad, your panel just needs to support one.",
                title: "Scan Alarm for Zones?",
                confirmButton: "Scan",
                cancelButton: "Cancel",
                post: false,
                confirm: function() {
                    $('#zone_scanning').spin('flower');
                    $('.progress_label').show();
                    $('#progressbar').show();
                    $('#progressbar').progressbar({
                        change: function() {
                            $('.progress_label').text("Current Progress: " + $('#progressbar').progressbar("value") + "%");
                        }
                    });
                    if (getZoneData() === false) {
                        alert('Unable to get partition Count, likely unsupported');
                    }
                },
                cancel: function() {
                    // No action on cancel
                }
            });
        });
        // Function to send imported zones to server and update table
        function populateZones(zones) {
            var dataString = JSON.stringify(zones);
            var table = $('#zones-table').DataTable();
            $.ajax({
                type: "POST",
                url: "{{ url_for('zones.import_zone') }}",
                data: dataString,
                contentType: 'application/json;charset=UTF-8',
                success: function(msg) {
                    state = states['importZonesDone'];
                    $('#zone_scanning').stop();
                    $('#zone_scanning').hide();
                    if (msg['success'] != 0) {
                        // Add each imported zone as a new row in the table
                        var addedZones = msg['success'];
                        for (var key in addedZones) {
                            if (addedZones.hasOwnProperty(key)) {
                                var entry = addedZones[key];
                                table.row.add([
                                    "<a href='/settings/zones/edit/" + entry['zone_id'] + "'>" + entry['zone_id'] + "</a>",
                                    entry['name'],
                                    entry['description'],
                                    "<a class='zone-remove-link' href='/settings/zones/remove/" + entry['zone_id'] + "'><img style='text-align: center; float: right; margin-right: 15px;' src='{{ url_for('static', filename='img/red_x.png') }}'/></a>"
                                ]).draw();
                            }
                        }
                    } else {
                        alert("No zones found, possible unsupported.");
                    }
                    $('.progress_label').hide();
                    $('#progressbar').hide();
                }
            });
        }
        // AJAX-based deletion for zone remove links (with confirmation)
        $('#zones-table').on('click', '.zone-remove-link', function(e) {
            e.preventDefault();
            var $link = $(this);
            var url = $link.attr('href');
            var $row = $link.closest('tr');
            $.confirm({
                title: "Delete Zone?",
                content: "Are you sure you want to delete this zone?",
                confirmButton: "Delete",
                cancelButton: "Cancel",
                post: false,
                confirm: function() {
                    $.ajax({
                        type: "POST",
                        url: url,
                        dataType: 'json',
                        success: function(response) {
                            if (response.success) {
                                // Remove the row from the DataTable
                                $('#zones-table').DataTable().row($row).remove().draw();
                            } else {
                                $.alert(response.error || "Failed to delete zone.");
                            }
                        },
                        error: function() {
                            $.alert("Failed to delete zone.");
                        }
                    });
                },
                cancel: function() {
                    // canceled - do nothing
                }
            });
        });
    });
</script>
