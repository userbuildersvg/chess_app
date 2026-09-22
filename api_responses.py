"""Build the small JSON response envelopes used by Zugzwang's API routes."""


def create_error_response(message, error_type=None, details=None):
    response = {
        'success': False,
        'message': message
    }

    if error_type:
        response['error_type'] = error_type

    if details:
        response.update(details)

    return response


def create_success_response(message, data=None):
    response = {
        'success': True,
        'message': message
    }

    if data:
        response.update(data)

    return response
