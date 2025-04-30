<script type="text/javascript">
    $(document).ready(function() {
        // Initialize DataTable with responsive behavior and state saving
        $.fn.spin.presets.flower = { lines: 13, length: 30, width: 10, radius: 30, className: 'spinner' };
        $('#loading').spin('flower');
        var table = $('#buttons-table').DataTable({
            responsive: true,
            stateSave: true,
            stateDuration: 60 * 60 * 24,
            pagingType: "full_numbers",
            language: {
                info: "_START_ to _END_ of _TOTAL_",
                infoEmpty: "No Results",
                infoFiltered: "",
                emptyTable: " "
            },
            initComplete: function() {
                // Hide spinner and show table once DataTable is ready
                $('#loading').stop().hide();
                $('#clear').css('display', 'inline-block');
                $('#datatable').show();
            }
        });

        // Helper function to show a Bootstrap modal with given content
        function showFormModal(title, content) {
            // Create modal structure if not present
            var $modal = $('#formModal');
            if (!$modal.length) {
                $modal = $(
                  '<div class="modal fade" id="formModal" tabindex="-1" role="dialog" aria-hidden="true">' +
                    '<div class="modal-dialog"><div class="modal-content">' +
                      '<div class="modal-header">' +
                        '<button type="button" class="close" data-dismiss="modal" aria-label="Close">' +
                          '<span aria-hidden="true">&times;</span>' +
                        '</button>' +
                        '<h4 class="modal-title"></h4>' +
                      '</div>' +
                      '<div class="modal-body"></div>' +
                      '<div class="modal-footer">' +
                        '<button type="button" class="btn btn-secondary" data-dismiss="modal">Close</button>' +
                      '</div>' +
                    '</div></div>' +
                  '</div>'
                );
                $('body').append($modal);
            }
            $modal.find('.modal-title').text(title);
            $modal.find('.modal-body').html(content);
            $modal.modal('show');
        }

        // Open "New Button" form in a modal via AJAX
        $('#clear').on('click', function(e) {
            e.preventDefault();
            $.get(this.href, function(formHtml) {
                showFormModal('New Keypad Button', formHtml);
            });
        });

        // Open "Edit Button" form in a modal via AJAX (delegated handler for dynamic content)
        $(document).on('click', 'a.edit-btn', function(e) {
            e.preventDefault();
            $.get(this.href, function(formHtml) {
                showFormModal('Edit Keypad Button', formHtml);
            });
        });

        // Handle form submission via AJAX (for both create and edit forms inside the modal)
        $(document).on('submit', '#formModal form', function(e) {
            e.preventDefault();
            var $form = $(this);
            var actionUrl = $form.attr('action');
            $.ajax({
                url: actionUrl,
                type: $form.attr('method') || 'POST',
                data: $form.serialize(),
                success: function(response) {
                    // On success, update the table and close the modal
                    if (response.success) {
                        if (response.action === "create") {
                            // Add the new button as a new row in the table
                            var b = response.button;
                            table.row.add([
                                b.id.toString(),
                                b.label,
                                b.code,
                                '<a href="{{ url_for("keypad.edit_button", button_id="__ID__") }}".replace("__ID__", b.id) + '" class="edit-btn" title="Edit">' +
                                    '<span class="glyphicon glyphicon-pencil"></span></a>&nbsp;' +
                                '<a href="{{ url_for("keypad.delete_button", button_id="__ID__") }}".replace("__ID__", b.id) + '" class="delete-btn" title="Delete">' +
                                    '<span class="glyphicon glyphicon-remove text-danger"></span></a>'
                            ]).draw();
                        } else if (response.action === "edit") {
                            // Update the existing row for the edited button
                            var b = response.button;
                            var $row = $('#buttons-table tbody tr[data-id="' + b.id + '"]');
                            if ($row.length) {
                                // Use DataTable API to update cell data
                                var rowIndex = table.row($row).index();
                                table.cell(rowIndex, 1).data(b.label);  // Label column
                                table.cell(rowIndex, 2).data(b.code);   // Code column
                                table.draw(false);
                            }
                        }
                        // Optionally display a success message (flash) to the user
                        if (response.message) {
                            // Append a temporary flash message element
                            var flashMsg = $('<div class="alert alert-success alert-dismissible" role="alert">' +
                                             '<button type="button" class="close" data-dismiss="alert" aria-label="Close"><span aria-hidden="true">&times;</span></button>' +
                                             response.message +
                                             '</div>');
                            $('.settings_wrapper').prepend(flashMsg);
                        }
                        $('#formModal').modal('hide');
                    }
                },
                error: function(xhr) {
                    if (xhr.status === 400) {
                        // Validation error: replace modal body with the returned form (which includes error messages)
                        $('#formModal .modal-body').html(xhr.responseText);
                    } else {
                        alert("An unexpected error occurred. Please try again.");
                    }
                }
            });
        });

        // Handle deletion via AJAX with confirmation
        $(document).on('click', 'a.delete-btn', function(e) {
            e.preventDefault();
            var $link = $(this);
            var $row = $link.closest('tr');
            $.confirm({
                title: 'Confirm Deletion',
                content: 'Are you sure you want to delete this button?',
                buttons: {
                    cancel: function() { /* Do nothing on cancel */ },
                    confirm: function() {
                        $.post($link.attr('href'), function(result) {
                            // On successful deletion, remove the row from the table
                            table.row($row).remove().draw(false);
                            if (result.message) {
                                var flashMsg = $('<div class="alert alert-success alert-dismissible" role="alert">' +
                                                 '<button type="button" class="close" data-dismiss="alert">&times;</button>' +
                                                 result.message +
                                                 '</div>');
                                $('.settings_wrapper').prepend(flashMsg);
                            }
                        });
                    }
                }
            });
        });
    });
</script>
