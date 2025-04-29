$(document).ready(function() {
    // Handle form submission for creating a zone
    $('#createZoneForm').submit(function(e) {
        e.preventDefault();

        $.ajax({
            url: '/zones',
            type: 'POST',
            data: JSON.stringify({
                zone_id: $('#zone_id').val(),
                name: $('#name').val(),
                description: $('#description').val()
            }),
            contentType: 'application/json',
            success: function(response) {
                alert('Zone created successfully');
                window.location.reload();
            },
            error: function(xhr) {
                alert('Error: ' + xhr.responseJSON.error);
            }
        });
    });
});
