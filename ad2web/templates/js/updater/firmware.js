<script type="text/javascript">
    // Flag to prevent multiple concurrent firmware uploads
    var firmwareuploading = false;

    // Utility: create clickable link HTML from URL and text
    function makelink(url, text) {
        return '<a href="' + url + '">' + text + '</a>';
    }

    // Utility: parse firmware file name to a version string (for .hex files)
    function parse_version(filename) {
        // Example: "ADEMCO_V2_2_8.hex" -> "2.2.8"
        var parts = filename.split('V');
        if (parts[1] !== undefined) {
            var versionParts = parts[1].split('.');
            var versionStr = versionParts[0].replace(/_/g, '.');
            return versionStr;
        }
        // If 'V' not found, just replace underscores in the whole string
        return parts[0].replace('_', '.');
    }

    // Update the firmware table to mark a new version as installed
    function update_table(newVersion) {
        var table = $('#firmware-table').DataTable();
        table.clear();
        // Re-populate rows using the global firmwareData
        if (firmwareData && firmwareData.firmware) {
            firmwareData.firmware.forEach(function(fw) {
                var versionCell = fw.version;
                if (fw.version === newVersion) {
                    // Highlight the newly installed version
                    versionCell += " (<span id='current_version2' style='color: green;'>Installed</span>)";
                }
                var notesLink = makelink(fw.notes, "Link");
                var fileLink = makelink(fw.file, "Download");
                table.row.add([ versionCell, notesLink, fw.tag, fw.panel_support, fileLink ]);
            });
        }
        table.draw();
    }

    $(document).ready(function() {
        // Grab references to progress dialog elements
        var upload_label = $('#progress_label');
        var upload_progressbar = $('#progress_bar');

        // Initialize DataTable for firmware list (with default sorting by Type column)
        $('#firmware-table').DataTable({
            order: [[2, "desc"]]
        });

        // If the firmware JSON could not be loaded (allOk is false), hide the JSON download form
        if (allOk === false) {
            $('#head').hide();
            $('#uploader_json').hide();
        }

        // Manual firmware upload form submission (AJAX)
        $('#uploader_manual').submit(function(e) {
            e.preventDefault();
            if (firmwareuploading === true) {
                $.alert('There is already a firmware upload in progress.');
                return false;
            }
            var filePath = $('#firmware_file').val();
            if (filePath === "") {
                $.alert('Please select a firmware file.');
            } else if (!filePath.endsWith('.hex')) {
                $.alert(filePath + ' is not a valid hex file.');
            } else {
                // Prepare file data for upload
                var fileObj = $('#firmware_file')[0].files[0];
                var formData = new FormData();
                formData.append('file', fileObj);
                // AJAX POST to upload the firmware file
                $.ajax({
                    url: "{{ url_for('update.update_firmware') }}",
                    type: 'POST',
                    data: formData,
                    processData: false,
                    contentType: false,
                    cache: false,
                    success: function(response) {
                        // Hide any loading indicators (if any were shown)
                        $('#loading').stop(); $('#loading').hide();
                        $('#downloading').hide();
                        if (response.uploading !== undefined) {
                            // Firmware file successfully received by server
                            var uploadedFileName = response.uploading;
                            var newVersion = parse_version(uploadedFileName);
                            // Open the progress dialog
                            $('#dialog').dialog({
                                title: 'Uploading Firmware',
                                height: 450,
                                width: 450,
                                buttons: {
                                    'retry': {
                                        text: 'Retry',
                                        id: 'btn-retry',
                                        click: function() {
                                            // Retry will re-initiate the firmware upload process
                                            upload_label.removeClass('upload-error');
                                            upload_progressbar.progressbar({ value: false });
                                            $('#btn-retry').button('disable');
                                            decoder.emit('firmwareupload');
                                        }
                                    }
                                },
                                close: function() {
                                    // On dialog close, reset progress and update UI with new version
                                    upload_label.removeClass('upload-error');
                                    upload_progressbar.progressbar({ value: false });
                                    $('#firmwarefile').empty();
                                    // Update the table and current version display if a new version was installed
                                    $('#current_version').text(newVersion);
                                    update_table(newVersion);
                                }
                            });
                            // Show initial status and start firmware upload via decoder
                            $('#firmwarefile').text("Writing: " + response.uploading);
                            decoder.emit('firmwareupload');  // trigger the actual firmware write process
                            // Disable and hide the retry button initially
                            $('#btn-retry').button('disable');
                            $('#btn-retry').hide();
                        } else {
                            // Did not get expected response (or got an error)
                            var errorMsg = 'Unexpected response from server. Please try again.';
                            if (response.error !== undefined && response.error === "NOFILE") {
                                errorMsg = 'Uploaded file could not be read by the server.';
                            }
                            $.alert(errorMsg);
                        }
                    },
                    error: function() {
                        $.alert('There was an error uploading your file. Please try again.');
                    }
                });
            }
            return false;
        });

        // Firmware selection (JSON) form submission (AJAX)
        $('#uploader_json').submit(function(e) {
            e.preventDefault();
            if (firmwareuploading === true) {
                $.alert('There is already firmware uploading in progress.');
                return false;
            }
            // Get the selected firmware version's display text (for later use)
            var selectedVersionText = $('#firmware_file_json option:selected').text();
            // Show downloading spinner and message
            $('#loading').show().spin('flower');
            $('#downloading').show().text("Downloading firmware from server...");
            // AJAX POST to trigger firmware download and preparation on server
            $.ajax({
                url: "{{ url_for('update.update_firmware') }}",
                type: "POST",
                data: $('#uploader_json').serialize(),  // send selected firmware choice
                success: function(response) {
                    // Hide the downloading indicator
                    $('#loading').stop(); $('#loading').hide();
                    $('#downloading').hide();
                    if (response.uploading !== undefined) {
                        // Firmware download succeeded and file is ready to upload to device
                        $('#dialog').dialog({
                            title: 'Uploading Firmware',
                            height: 450,
                            width: 450,
                            buttons: {
                                'retry': {
                                    text: 'Retry',
                                    id: 'btn-retry',
                                    click: function() {
                                        upload_label.removeClass('upload-error');
                                        upload_progressbar.progressbar({ value: false });
                                        $('#btn-retry').button('disable');
                                        decoder.emit('firmwareupload');
                                    }
                                }
                            },
                            close: function() {
                                // Reset and update UI on dialog close
                                upload_label.removeClass('upload-error');
                                upload_progressbar.progressbar({ value: false });
                                $('#firmwarefile').empty();
                                // Update current version to the newly installed one
                                $('#current_version').text(selectedVersionText);
                                update_table(selectedVersionText);
                            }
                        });
                        $('#firmwarefile').text("Writing: " + response.uploading);
                        decoder.emit('firmwareupload');
                        $('#btn-retry').button('disable');
                        $('#btn-retry').hide();
                    } else {
                        // If something went wrong (no 'uploading' field), alert an error
                        $.alert('There was an issue processing the firmware file. Please try again.');
                    }
                },
                error: function() {
                    $.alert('There was an error downloading the firmware. Please check your connection and try again.');
                }
            });
            return false;
        });

        // Subscribe to firmware upload progress events (broadcast from the server)
        PubSub.subscribe('firmwareupload', function(msgType, msg) {
            var stage = msg.stage;
            firmwareuploading = true;  // mark that an upload is in progress
            if (stage === "STAGE_START") {
                upload_label.text("Starting upload...");
            } else if (stage === "STAGE_WAITING") {
                upload_label.text("Waiting for device...");
            } else if (stage === "STAGE_BOOT") {
                upload_label.text("Rebooting device...");
            } else if (stage === "STAGE_LOAD") {
                upload_label.text("Waiting for bootloader...");
            } else if (stage === "STAGE_UPLOADING") {
                // Update progress percentage
                upload_label.text("Uploading firmware: " + msg.percent + "%");
                upload_progressbar.progressbar({ value: msg.percent });
            } else if (stage === "STAGE_DONE") {
                upload_label.text("Firmware upload complete!");
                // Ensure retry button is disabled/hidden when done
                $('#btn-retry').button('disable').hide();
                upload_progressbar.progressbar({ value: 100 });
            } else if (stage === "STAGE_CONFIGURE") {
                upload_label.text("Reconfiguring device...");
            } else if (stage === "STAGE_FINISHED") {
                upload_label.text("Complete!");
                firmwareuploading = false;  // upload process finished
            } else if (stage === "STAGE_ERROR") {
                console.error(msg.error || "Unknown error");
                upload_label.text(msg.error || "Firmware upload error.");
                upload_label.addClass('upload-error');
                upload_progressbar.progressbar({ value: -1 });  // indicate error on progress bar
                $('#btn-retry').button('enable').show();       // allow retry
                firmwareuploading = false;
            }
        });
    });
</script>
