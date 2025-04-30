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
            infoEmpty: "No Results",
            emptyTable: " ",
            infoFiltered: ""
        },
        columnDefs: [
            { orderable: false, targets: -1 }  // disable sorting on Actions column
        ],
        initComplete: function() {
            // Hide spinner and show table & "New User" button when done
            $('#loading').stop().hide();
            $('#datatable').show();
            $('#clear').css('display', 'inline-block');
        }
    });

    // CSRF token for AJAX posts (from hidden input in page)
    var csrftoken = $('#csrf_token').val();

    // Handle user status toggle via AJAX
    $('#users-table').on('click', '.toggle-status', function(e) {
        e.preventDefault();
        var $link = $(this);
        var url = $link.data('url');
        $.ajax({
            url: url,
            type: 'POST',
            data: { csrf_token: csrftoken },
            success: function(response) {
                if (response.success) {
                    // Update status text and data attribute in UI
                    $link.text(response.status_label);
                    $link.closest('td').attr('data-status-code', response.status);
                } else {
                    alert(response.message || 'Failed to toggle user status.');
                }
            },
            error: function() {
                alert('Error toggling user status.');
            }
        });
    });

    // Handle user deletion via AJAX
    $('#users-table').on('click', '.delete-user', function(e) {
        e.preventDefault();
        var $link = $(this);
        var url = $link.attr('href');
        var $row = $link.closest('tr');
        if (confirm("Are you sure you want to delete this user?")) {
            $.ajax({
                url: url,
                type: 'POST',
                data: { csrf_token: csrftoken },
                success: function(response) {
                    if (response.success) {
                        // Remove the user's row from the table
                        userTable.row($row).remove().draw(false);
                    } else {
                        alert(response.message || 'User could not be deleted.');
                    }
                },
                error: function() {
                    alert('Error deleting user.');
                }
            });
        }
    });
});
