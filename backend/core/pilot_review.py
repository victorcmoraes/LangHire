"""Small, factual helpers shared by the supervised pilot and its fixtures."""
import re
import json


def accepted_brazil_location(token, city, state):
    """Lever enriches address metadata asynchronously; validate its meaning."""
    try:
        location = json.loads(token)
        address = location.get('address')
        if not isinstance(address,dict):
            # The current hosted form initially stores only the chosen place ID
            # and display name. Enrichment is optional and may never arrive.
            name=re.compile(r'^\s*'+re.escape(city)+r'\s*,\s*'+re.escape(state)+r'\s*,\s*(?:Brazil|Brasil|BRA|BR)\s*$',re.I)
            return bool(location.get('id') and name.fullmatch(location.get('name','')))
        region=address.get('region',{})
        return (address['city'].casefold() == city.casefold()
                and state.casefold() in {str(region.get('code','')).casefold(),str(region.get('name','')).casefold()}
                and address['country']['code'] in ('BR','BRA')
                and bool(brazil_location_pattern(city, state).search(location['name'])))
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def brazil_location_pattern(city, state):
    if not city or not state:
        raise ValueError("A confirmed city and state are required")
    return re.compile(re.escape(city) + r".*" + re.escape(state) + r".*(?:Brazil|Brasil|\bBRA\b)", re.I)
