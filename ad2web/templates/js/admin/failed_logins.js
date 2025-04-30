$(document).ready(function() {
    // Initialize spinner and DataTable for Failed Logins list
    $.fn.spin.presets.flower = { lines: 13, length: 30, width: 10, radius: 30, className: 'spinner' };
    $('#loading').spin('flower');
    $('#failed-table').DataTable({
        responsive: true,
        stateSave: true,
        stateDuration: 60 * 60 * 24,  // preserve table state for 1 day
        pagingType: "full_numbers",
        language: {
            info: "_START_ to _END_ of _TOTAL_",
            infoEmpty: "No Results",
            emptyTable: " ",
            infoFiltered: ""
        },
        initComplete: function() {
            // Hide spinner and show table when initialization is complete
            $('#loading').stop().hide();
            $('#datatable').show();
        }
    });
});
