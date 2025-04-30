$(document).ready(function() {
    // Prevent double-submit on Next buttons by disabling them after click
    $('.form-box').on('submit', function() {
        $(this).find('input[type=submit]').prop('disabled', true);
    });
    // (Additional common setup behaviors can be added here as needed)
});
