<script type="text/javascript">
$(document).ready(function() {
    // Subscribe to test result events and update the table
    PubSub.subscribe('test', function(type, msg) {
        var resultSymbols = {
            'PASS': '<span style="color:green">&#10004;</span>',
            'FAIL': '<span style="color:red">&#10008;</span>',
            'TIMEOUT': '<span style="color:orange">&#9888;</span>'
        };
        // Find the table row corresponding to this test and update result and details
        var $row = $('tr#test-' + msg.test);
        $row.find('td.test-results').html(resultSymbols[msg.results] || msg.results);
        $row.find('td.details').text(msg.details || '');

        // If this is the final test result (Receive), re-enable the Run Tests button
        if (msg.test === 'recv') {
            $('#run-tests').prop('disabled', false);
        }
    });

    // Handle the "Run Tests" button click
    $('#run-tests').click(function() {
        // Reset previous results to spinner icons
        $('td.test-results').html('<img src="{{ url_for("static", filename="img/spinner.gif") }}" alt="in progress">');
        $('td.details').empty();
        // Disable the Run Tests button to prevent multiple concurrent runs
        $(this).prop('disabled', true);
        // Emit the test event to start device tests
        if (typeof decoder !== 'undefined') {
            decoder.emit('test');
        }
    });
});
</script>
