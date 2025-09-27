"""
Utility functions for chess application
"""

def create_error_response(message, error_type=None, details=None):
    """Create standardized error response"""
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
    """Create standardized success response"""
    response = {
        'success': True,
        'message': message
    }
    
    if data:
        response.update(data)
    
    return response