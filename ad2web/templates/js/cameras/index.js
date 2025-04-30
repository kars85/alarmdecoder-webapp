<script type="text/javascript">
var RED_X_URL = "{{ url_for('static', filename='img/red_x.png') }}";

$(document).ready(function() {
    // Define a spinner preset and show loading spinner
    $.fn.spin.presets.flower = { lines: 13, length: 30, width: 10, radius: 30, className: 'spinner' };
    $('#loading').spin('flower');
    // Initialize DataTable with state save and responsive behavior
    var table = $('#cameras-table').DataTable({
        responsive: true,
        stateSave: true,
        stateDuration: 60 * 60 * 24,  // save state for 24 hours
        pagingType: "full_numbers",
        language: {
            info: "_START_ to _END_ of _TOTAL_",
            infoFiltered: "",
            infoEmpty: "No Results",
            emptyTable: "No cameras available."
        },
        initComplete: function() {
            // Hide spinner and show table when initialization is done
            $('#loading').stop().hide();
            $('#datatable').show();
            $('#clear').css('display', 'inline-block');
        }
    });

    // Helper to attach confirmation dialog to delete links (for initial and dynamic elements)
    function attachConfirmDelete($elements) {
        $elements.confirm({
            title: "Confirm Deletion",
            text: "Are you sure you want to delete this camera?",
            confirmButton: "Yes", cancelButton: "Cancel",
            confirm: function(button) {
                var url = $(button).attr('href');
                $.post(url, function(response) {
                    if (response.success) {
                        // Remove the camera's row from the table
                        table.row('#camera-' + response.id).remove().draw();
                        $('#message').html('<div class="alert alert-info">' + response.message + '</div>');
                    } else {
                        $('#message').html('<div class="alert alert-danger">' + (response.message || 'Failed to delete camera.') + '</div>');
                    }
                });
            },
            cancel: function(button) {
                /* no action on cancel */
            },
            post: false  // we'll handle the request via AJAX manually
        });
    }

    // Apply confirm handler to existing delete buttons
    attachConfirmDelete($('.delete-camera'));

    // "New Camera" button click -> open modal with create form
    $('#clear').on('click', function(e) {
        e.preventDefault();
        $('#cameraModal .modal-title').text('New Camera');
        $.get($(this).attr('href'), function(formHtml) {
            $('#cameraModal .modal-body').html(formHtml);
            // Ensure cancel button in form closes the modal instead of navigating
            $('#cameraModal').find('button[name="cancel"]').off('click').on('click', function(evt) {
                evt.preventDefault();
                $('#cameraModal').modal('hide');
            });
            $('#cameraModal').modal('show');
        });
    });

    // Edit link click -> open modal with edit form
    $(document).on('click', 'a.edit-camera', function(e) {
        e.preventDefault();
        $('#cameraModal .modal-title').text('Edit Camera');
        $.get($(this).attr('href'), function(formHtml) {
            $('#cameraModal .modal-body').html(formHtml);
            $('#cameraModal').find('button[name="cancel"]').off('click').on('click', function(evt) {
                evt.preventDefault();
                $('#cameraModal').modal('hide');
            });
            $('#cameraModal').modal('show');
        });
    });

    // Handle form submissions (for both create and edit forms loaded in the modal)
    $(document).on('submit', 'form.form-box', function(e) {
        e.preventDefault();
        var $form = $(this);
        var actionUrl = $form.attr('action');
        var formData = $form.serialize();
        $.ajax({
            url: actionUrl,
            type: 'POST',
            data: formData,
            success: function(response) {
                if (response.success) {
                    // On success, update the table
                    if (response.camera) {
                        var cam = response.camera;
                        if (actionUrl.indexOf('/create_camera') !== -1) {
                            // New camera added: append a new row to the table
                            var actionsHtml =
                                '<a href="' + url_for('cameras.edit_camera', {'id': cam.id}) + '" ' +
                                   'class="edit-camera" data-id="' + cam.id + '">Edit</a>' +
                                ' | <a href="' + url_for('cameras.remove_camera', {'id': cam.id}) + '" ' +
                                   'class="delete-camera" data-id="' + cam.id + '">' +
                                   '<img src="' + RED_X_URL + '" alt="Delete"/></a>';
                            // Add the new row data
                            var newRowNode = table.row.add([
                                cam.id, cam.name, cam.url || cam.get_jpg_url || "", actionsHtml
                            ]).draw().node();
                            $(newRowNode).attr('id', 'camera-' + cam.id);
                            // Attach confirm dialog to the new delete button
                            attachConfirmDelete($(newRowNode).find('.delete-camera'));
                        } else {
                            // Camera edited: update the existing row
                            var rowNode = $('#camera-' + cam.id);
                            if (rowNode.length) {
                                // Update table data (reuse existing actions HTML)
                                var currentRow = table.row(rowNode);
                                var currentData = currentRow.data();
                                currentRow.data([
                                    cam.id, cam.name, cam.url || cam.get_jpg_url || "", currentData[3]
                                ]).draw();
                                // Reattach confirm (row HTML is refreshed)
                                attachConfirmDelete($(rowNode).find('.delete-camera'));
                            }
                        }
                    }
                    $('#message').html('<div class="alert alert-success">' + response.message + '</div>');
                    $('#cameraModal').modal('hide');
                } else {
                    // Unexpected failure (e.g. service returned error)
                    $('#message').html('<div class="alert alert-danger">' + (response.message || 'Operation failed.') + '</div>');
                    $('#cameraModal').modal('hide');
                }
            },
            error: function(xhr) {
                if (xhr.status === 400) {
                    // Validation errors: reload form with error messages
                    $('#cameraModal .modal-body').html(xhr.responseText);
                    // Re-bind cancel button behavior
                    $('#cameraModal').find('button[name="cancel"]').off('click').on('click', function(evt) {
                        evt.preventDefault();
                        $('#cameraModal').modal('hide');
                    });
                } else {
                    // Other errors
                    $('#message').html('<div class="alert alert-danger">An error occurred. Please try again.</div>');
                    $('#cameraModal').modal('hide');
                }
            }
        });
    });
});
</script>
