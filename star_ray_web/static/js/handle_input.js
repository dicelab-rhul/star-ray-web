


function handleMouseEnter(event) {

}

function handleMouseExitWindow(event) {
    console.log(event)
}

function handleMouseMotion(event) {
    var element = event.target;
    var data = {
        id: element.id,
        type: element.tagName,
        position: { x: event.clientX, y: event.clientY },
        relative: { x: event.movementX, y: event.movementY },
    }
    push_input_event('/on_mouse_motion', data)
}

function handleMouseButton(event, status) {
    var element = event.target;
    var data = {
        id: element.id,
        type: element.tagName,
        status: status,
        position: { x: event.clientX, y: event.clientY },
        button: event.button,
    }
    push_input_event('/on_mouse_button', data)
}

function handleMouseDown(event) {
    handleMouseButton(event, "down")
}

function handleMouseUp(event) {
    handleMouseButton(event, "up")
}

function handleMouseClick(event) {
    handleMouseButton(event, "click")
}

function push_input_event(route, data) {
    fetch(route, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    })
        .then(response => response.json())
        .then(_ => { }) // nothing to do here
        .catch(error => {
            console.error('Error:', error);
        });
}
