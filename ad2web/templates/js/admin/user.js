<script type="text/javascript">
$(document).ready(function() {
    // Initialize spinner and DataTable for Users list
    $.fn.spin.presets.flower = { lines: 13, length: 30, width: 10, radius: 30, className: 'spinner' };
    $('#loading').spin('flower');
    var userTable = $('#users-table').DataTable({
        responsive: true,
        stateSave: true,
        stateDuration: 60 * 60 * 24,  // save state for 1 day
        pagingType: "full_numbers",
        language: {
            info: "_START_ to _END_ of _TOTAL_",
            infoFiltered: "",
            infoEmpty: "No Results",
            emptyTable: " "
        },
        initComplete: function() {
            $('#loading').stop().hide();
            $('#datatable').show();
        }
    });

    // Toggle user status via AJAX
    $('.toggle-status').on('click', function(e) {
        e.preventDefault();
        var $link = $(this);
        var userId = $link.closest('td').data('user-id');
        $.post('{{ url_for("users.toggle_status", user_id=0) }}'.replace('/0/toggle', '/' + userId + '/toggle'), function(response) {
            if (response.success) {
                // Update status text in UI
                $link.text(response.new_status);
            } else {
                alert('Failed to toggle user status.');
            }
        });
    });

    // Delete user via AJAX with confirmation
    $('.delete-user').on('click', function(e) {
        e.preventDefault();
        var $link = $(this);
        var userId = $link.closest('tr').find('.status-cell').data('user-id');
        if (confirm("Are you sure you want to delete this user?")) {
            $.post('{{ url_for("users.delete", user_id=0) }}'.replace('/0', '/' + userId), function(response) {
                if (response.success) {
                    // Remove the user's row from the table
                    userTable.row($link.closest('tr')).remove().draw(false);
                    flashMessage('User deleted.', 'success');  // optional: a function to show flash messages via JS
                } else {
                    alert('User could not be deleted.');
                }
            });
        }
    });
});
</script>
