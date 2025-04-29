<script type="text/javascript">
$(document).ready(function() {
    // Initialize spinner and DataTable for zones list
    $.fn.spin.presets.flower = { lines: 13, length: 30, width: 10, radius: 30, className: 'spinner' };
    $('#loading').spin('flower');
    var zoneTable = $('#zones-table').DataTable({
        responsive: true,
        stateSave: true,
        stateDuration: 60 * 60 * 24,
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

    // Confirm and trigger panel scan (import zones)
    $('#importZone').on('click', function() {
        $.confirm({
            content: "This will take a few minutes and will replace existing zones with those detected on the panel. Continue?",
            title: "Confirm Scan",
            confirm: function() {
                // Show scanning progress
                $('.progress_label').text('Scanning...').show();
                $('#progressbar').show().spin('flower');
                $.ajax({
                    type: "POST",
                    url: "{{ url_for('zones.import_zone') }}",
                    dataType: "json",
                    data: JSON.stringify({}),  // send an empty JSON object to initiate scan
                    contentType: "application/json"
                }).done(function(response) {
                    $('#progressbar').stop().hide();
                    $('.progress_label').hide();
                    if (response.success && typeof(response.success) === 'object') {
                        // Successfully imported zones, update table
                        zoneTable.clear();
                        $.each(response.success, function(zoneId, zoneData) {
                            var actionHtml = '<a href="/settings/zones/delete/' + zoneId + '" class="delete-zone">'
                                           + '<img src="/static/img/red_x.png" alt="Delete"/></a>';
                            zoneTable.row.add([
                                '<a href="/settings/zones/edit/' + zoneId + '">' + zoneId + '</a>',
                                zoneData.name,
                                zoneData.description,
                                actionHtml
                            ]);
                        });
                        zoneTable.draw(false);
                        flashMessage('Zones imported successfully.', 'success');
                    } else if (response.success === 0) {
                        flashMessage('No new zones found.', 'info');
                    } else if (response.success) {
                        // If response.success is a string, treat as error message
                        alert(response.success);
                    } else {
                        alert('Failed to import zones.');
                    }
                });
            }
        });
    });

    // Delete zone via AJAX with confirmation
    $('.delete-zone').on('click', function(e) {
        e.preventDefault();
        var $link = $(this);
        var zoneId = $link.closest('tr').find('td:first-child a').text();
        if (confirm("Are you sure you want to delete zone " + zoneId + "?")) {
            $.post('{{ url_for("zones.delete", zone_id=0) }}'.replace('/0', '/' + zoneId), function(response) {
                if (response.success) {
                    zoneTable.row($link.closest('tr')).remove().draw(false);
                    flashMessage('Zone deleted.', 'success');
                } else {
                    alert('Failed to delete zone.');
                }
            });
        }
    });
});
</script>
