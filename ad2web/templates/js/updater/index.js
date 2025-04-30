<script type="text/javascript">
    // Polling function to detect when the app has finished restarting
    var timeoutVar;
    function poll_app_restarted() {
        $.ajax({
            url: "{{ url_for('update.checkavailable') }}",
            type: "GET",
            timeout: 3000,
            statusCode: {
                // If the application is still restarting and not responding
                502: function() {
                    // Show spinner and message, then keep polling
                    $('#app_running').html(
                        '<img src="{{ url_for("static", filename="img/spinner.gif") }}"> Application is restarting...'
                    );
                    timeoutVar = setTimeout(poll_app_restarted, 5000);
                }
            },
            success: function(data) {
                var jsondata = JSON.parse(data);
                if (jsondata['status'] && jsondata['status'] === 'PASS') {
                    // Application is back up
                    clearTimeout(timeoutVar);
                    window.location.reload();
                }
            },
            complete: function(xhr, status) {
                if (status === 'timeout') {
                    console.log('Restart check timed out, retrying...');
                    timeoutVar = setTimeout(poll_app_restarted, 1000);
                }
            }
        });
    }

    // Initialize Update/Restart buttons for a component
    function build_update_button(component, enabled) {
        if (enabled === true) {
            // Show the Update button if an update is available
            $('#' + component + '-update-submit').show();
        }
        // Handle Update button click
        $('#' + component + '-update-submit').click(function() {
            // Clear any status icon and hide the button to prevent duplicate clicks
            $('#' + component + '-status').html('');
            $('#' + component + '-update-submit').hide();
            $('#' + component + '-update-anim').show();  // show spinner

            // AJAX call to perform the update on the server
            $.ajax({
                url: "{{ url_for('update.update') }}",
                type: 'POST',
                data: JSON.stringify({ 'component': component }),
                contentType: 'application/json;charset=UTF-8',
                success: function(data) {
                    var jsondata = JSON.parse(data);
                    if (jsondata[component]['status'] === 'PASS') {
                        // Update successful: show green check
                        $('#' + component + '-status').html('<span style="color:green">&#10004;</span>');
                        if (jsondata[component]['restart_required'] === true) {
                            // If this component requires an application restart, show the Restart button
                            $('#' + component + '-restart-submit').show();
                        }
                    } else {
                        // Update failed: show red X and re-enable Update button for retry
                        $('#' + component + '-status').html('<span style="color:red">&#10008;</span>');
                        $('#' + component + '-update-submit').show();
                    }
                },
                complete: function() {
                    // Hide spinner after response
                    $('#' + component + '-update-anim').hide();
                }
            });
            return false;  // prevent default form submission
        });

        // Handle Restart button click (if shown)
        $('#' + component + '-restart-submit').click(function() {
            $('#' + component + '-status').html('');
            $('#' + component + '-restart-submit').hide();
            $('#' + component + '-update-anim').show();  // show spinner during restart

            // AJAX call to trigger application restart
            $.ajax({
                url: "{{ url_for('update.restart') }}",
                type: 'POST',
                data: JSON.stringify({ 'component': component }),
                contentType: 'application/json;charset=UTF-8',
                complete: function() {
                    // On response (likely after sending restart signal), disconnect decoder and poll for app restart
                    decoder.disconnect();
                    $('#app_running').html(
                        '<img src="{{ url_for("static", filename="img/spinner.gif") }}"> Application is restarting...'
                    );
                    timeoutVar = setTimeout(poll_app_restarted, 5000);
                }
            });
            return false;
        });
    }

    // On page ready, set up each component's buttons based on whether an update is needed
    $(document).ready(function() {
        {% for component, (needs_update, branch, revision, new_revision, status, project_url) in updates.items() %}
            build_update_button('{{ component }}', {{ 'true' if needs_update else 'false' }});
        {% endfor %}
    });
</script>
