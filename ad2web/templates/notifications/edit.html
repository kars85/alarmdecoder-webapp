<script type="text/javascript">
$(document).ready(function(){
    // Initialize DataTable for notifications table (preserve state for 1 day)
    $('#notifications-table').dataTable({
        responsive: true,
        stateSave: true,
        stateDuration: 60 * 60 * 24,  // 1 day
        pagingType: "full_numbers",
        language: {
            infoEmpty: "No Results",
            infoFiltered: "",
            info: "_START_ to _END_ of _TOTAL_",
            emptyTable: " "
        },
        initComplete: function() {
            $('#loading').hide();
            $('#datatable').show();
        }
    });

    // New Notification button opens create form modal
    $('#new-notif-btn').on('click', function(e){
        e.preventDefault();
        var url = $(this).data('url');
        // Load the create form via AJAX into the modal
        $('#notification-modal .modal-content').load(url, function(){
            $('#notification-modal').modal('show');
        });
    });

    // Edit notification link opens edit form modal
    $('.edit-notif-link').on('click', function(e){
        e.preventDefault();
        var url = $(this).attr('href');
        $('#notification-modal .modal-content').load(url, function(){
            $('#notification-modal').modal('show');
        });
    });

    // Handle form submissions (create/edit) via AJAX
    $('#notification-modal').on('submit', 'form', function(e){
        e.preventDefault();
        var $form = $(this);
        var url = $form.attr('action');
        var formData = $form.serialize();
        $.post(url, formData, function(response){
            if (response.status === 'created' || response.status === 'updated') {
                // If zone filter step is required, load it in modal
                if (response.zone_filter_required) {
                    // Load zone filter form in modal
                    $('#notification-modal .modal-content').load(response.zone_filter_url, function(){
                        // Ensure modal remains open for zone selection
                    });
                } else {
                    // Update the table dynamically
                    if (response.status === 'created') {
                        // Add new notification row
                        var newRow = '<tr data-id="'+ response.id +'">';
                        newRow += '<td>'+ response.type +'</td>';
                        newRow += '<td>'+ (response.owner || '{{ current_user.name }}') +'</td>';  // if owner not provided, assume current user
                        newRow += '<td><a href="{{ url_for("notifications.edit", id=0) }}'.replace('0', response.id) + '" class="edit-notif-link">'+ response.description +'</a></td>';
                        newRow += '<td style="text-align:center;">';
                        newRow += '<a href="{{ url_for("notifications.toggle_notification", id=0) }}'.replace('0', response.id) + '" class="toggle-notif" title="Disable">';
                        newRow += '<span style="color:green; font-size: large;">&#10004;</span></a>&nbsp;&nbsp;';
                        newRow += '<a href="{{ url_for("notifications.copy_notification", id=0) }}'.replace('0', response.id) + '" class="copy-notif" title="Copy">';
                        newRow += '<img src="{{ url_for("static", filename="img/copy.png") }}" alt="Copy"/></a>&nbsp;&nbsp;';
                        newRow += '<a href="#" data-href="{{ url_for("notifications.remove", id=0) }}'.replace('0', response.id) + '" class="remove-notif" title="Remove">';
                        newRow += '<img src="{{ url_for("static", filename="img/red_x.png") }}" alt="Remove"/></a></td></tr>';
                        $('#notifications-table').DataTable().row.add($(newRow)).draw();
                    } else if (response.status === 'updated') {
                        // If updated, just update the description text or enabled status in the existing row
                        var $row = $('#notifications-table').find('tr[data-id="'+ response.id +'"]');
                        $row.find('td:nth-child(3) a').text(response.description);
                        // If notification was disabled and is now enabled (edited forms always re-enable notifications):
                        if (response.enabled) {
                            $row.find('.toggle-notif span').css('color','green').attr('title','Disable');
                        }
                    }
                    $('#notification-modal').modal('hide');
                }
            }
        }, 'json');
    });

    // Delete (remove) notification with confirmation via AJAX
    $('body').on('click', '.remove-notif', function(e){
        e.preventDefault();
        var $link = $(this);
        var deleteUrl = $link.data('href');
        $.confirm({
            title: "Delete Notification",
            content: "Are you sure you want to delete this notification?",
            buttons: {
                confirm: function(){
                    $.ajax({
                        url: deleteUrl,
                        type: 'POST',
                        success: function(response){
                            // Remove the row from DataTable
                            var $row = $link.closest('tr');
                            $('#notifications-table').DataTable().row($row).remove().draw();
                        }
                    });
                },
                cancel: function(){}
            }
        });
    });

    // Toggle notification enable/disable without page reload
    $('body').on('click', '.toggle-notif', function(e){
        e.preventDefault();
        var $link = $(this);
        $.post($link.attr('href'), function(response){
            if(response.status){
                // Flip the checkmark color based on new status
                if(response.status === 'enabled'){
                    $link.find('span').css('color','green');
                    $link.attr('title','Disable');
                } else if(response.status === 'disabled'){
                    $link.find('span').css('color','red');
                    $link.attr('title','Enable');
                }
            } else {
                // If no JSON response (non-AJAX fallback), reload page
                location.reload();
            }
        }, 'json');
    });

    // Copy notification via AJAX
    $('body').on('click', '.copy-notif', function(e){
        e.preventDefault();
        var $link = $(this);
        $.post($link.attr('href'), function(response){
            if(response.status === 'cloned'){
                // Add the cloned notification as a new row
                var newRow = '<tr data-id="'+ response.id +'">';
                newRow += '<td>'+ (response.type ? '{{ NOTIFICATION_TYPES[0] }}'.replace('0', response.type) : '') +'</td>';  // type might be numeric code
                newRow += '<td>'+ (response.owner || '{{ current_user.name }}') +'</td>';
                newRow += '<td><a href="{{ url_for("notifications.edit", id=0) }}'.replace('0', response.id) + '" class="edit-notif-link">'+ response.description +'</a></td>';
                newRow += '<td style="text-align:center;">';
                newRow += '<a href="{{ url_for("notifications.toggle_notification", id=0) }}'.replace('0', response.id) + '" class="toggle-notif" title="Disable">';
                newRow += '<span style="color:green; font-size: large;">&#10004;</span></a>&nbsp;&nbsp;';
                newRow += '<a href="{{ url_for("notifications.copy_notification", id=0) }}'.replace('0', response.id) + '" class="copy-notif" title="Copy">';
                newRow += '<img src="{{ url_for("static", filename="img/copy.png") }}" alt="Copy"/></a>&nbsp;&nbsp;';
                newRow += '<a href="#" data-href="{{ url_for("notifications.remove", id=0) }}'.replace('0', response.id) + '" class="remove-notif" title="Remove">';
                newRow += '<img src="{{ url_for("static", filename="img/red_x.png") }}" alt="Remove"/></a></td></tr>';
                $('#notifications-table').DataTable().row.add($(newRow)).draw();
            }
        }, 'json');
    });
});
</script>
