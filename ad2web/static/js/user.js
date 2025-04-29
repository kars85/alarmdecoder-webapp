$(document).ready(function() {
    // Handle form submission for creating a user
    $('#createUserForm').submit(function(e) {
        e.preventDefault();

        $.ajax({
            url: '/users',
            type: 'POST',
            data: JSON.stringify({
                name: $('#name').val(),
                email: $('#email').val(),
                password: $('#password').val()
            }),
            contentType: 'application/json',
            success: function(response) {
                alert('User created successfully');
                window.location.reload();
            },
            error: function(xhr) {
                alert('Error: ' + xhr.responseJSON.error);
            }
        });
    });
});
