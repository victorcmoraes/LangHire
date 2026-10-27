"""Small, factual helpers shared by the supervised pilot and its fixtures."""
import re
import json


def accepted_brazil_location(token, city, state):
    """Lever enriches address metadata asynchronously; validate its meaning."""
    try:
        location = json.loads(token)
        address = location['address']
        return (address['city'].casefold() == city.casefold()
                and address['region']['code'].casefold() == state.casefold()
                and address['country']['code'] == 'BR'
                and bool(brazil_location_pattern(city, state).search(location['name'])))
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def brazil_location_pattern(city, state):
    if not city or not state:
        raise ValueError("A confirmed city and state are required")
    return re.compile(re.escape(city) + r".*" + re.escape(state) + r".*(?:Brazil|Brasil|\bBRA\b)", re.I)
