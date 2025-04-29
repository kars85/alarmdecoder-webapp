<script type="text/javascript">
    $(document).ready(function() {
        // Initialize loading spinner and DataTable (existing functionality)
        $.fn.spin.presets.flower = {
            lines: 13, length: 30, width: 10, radius: 30, className: 'spinner'
        }
        $('#loading').spin('flower');
        var table = $('#notifications-table').DataTable({  // using DataTable API
            responsive: true,
            stateSave: true,
            stateDuration: 60 * 60 * 24,  // 1 day
            pagingType: "full_numbers",
            language: {
                infoEmpty: "No Results",
                infoFiltered: "",
                emptyTable: " ",
                info: "_START_ to _END_ of _TOTAL_"
            },
            columns: [
                { "width": "15%" },
                { "width": "15%" },
                null,
                { "width": "15%" }
            ],
            initComplete: function() {
                $('#loading').stop().hide();
                $('#clear').css('display', 'inline-block');
            }
        });

        // Handle toggle (enable/disable) via AJAX
        $('.toggle-notif').on('click', function(e) {
            e.preventDefault();
            var $link = $(this);
            var notifId = $link.data('id');
            $.ajax({
                url: $link.attr('href'),
                type: 'POST',  // use POST for state-changing action
                dataType: 'json',
                headers: {
                    // If CSRF tokens are in use, include it from a cookie or meta tag
                    'X-CSRFToken': (document.cookie.match(/csrf_token=([^;]+)/) || [null, ''])[1]
                },
                success: function(response) {
                    if (response.enabled !== undefined) {
                        // Toggle the icon color and title based on new status
                        var $icon = $link.find('span');
                        if (response.enabled) {
                            $icon.css('color', 'green').attr('title', 'Disable');
                        } else {
                            $icon.css('color', 'red').attr('title', 'Enable');
                        }
                    }
                },
                error: function(xhr) {
                    console.error("Toggle failed:", xhr.statusText);
                    alert("Failed to toggle notification (ID " + notifId + ").");
                    // On error, we can refresh the page to reflect any potential state change or revert.
                }
            });
        });

        // Handle notification deletion via AJAX with confirmation
        $('.remove-notif').on('click', function(e) {
            e.preventDefault();
            var $link = $(this);
            var notifId = $link.data('id');
            if (!confirm("Are you sure you want to delete this notification?")) {
                return;  // user canceled deletion
            }
            $.ajax({
                url: $link.attr('href'),
                type: 'POST',
                dataType: 'json',
                headers: {
                    'X-CSRFToken': (document.cookie.match(/csrf_token=([^;]+)/) || [null, ''])[1]
                },
                success: function(response) {
                    // Remove the row from the DataTable
                    var rowSelector = '#notification-' + notifId;
                    table.row($(rowSelector)).remove().draw(false);
                    // Optionally, show a message (the server already flashed a message;
                    // if we want to surface it without reload, we could display it here).
                },
                error: function(xhr) {
                    console.error("Delete failed:", xhr.statusText);
                    alert("Failed to delete notification (ID " + notifId + ").");
                }
            });
        });
    });
</script>
