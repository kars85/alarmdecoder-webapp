<script type="text/javascript">
$(document).ready(function() {
    // Cache the username/password field containers for toggling
    var $usernameGroup = $('#username').closest('.control-group');
    var $passwordGroup = $('#password').closest('.control-group');

    function toggleAuthFields() {
        if ($('#auth_required').is(':checked')) {
            $usernameGroup.show();
            $passwordGroup.show();
        } else {
            $usernameGroup.hide();
            $passwordGroup.hide();
        }
    }

    // Initialize visibility on page load
    toggleAuthFields();

    // Toggle on checkbox state change
    $('#auth_required').change(function() {
        toggleAuthFields();
    });
});
</script>
