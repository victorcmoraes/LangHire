from core.pilot_review import brazil_location_pattern
from core.pilot_review import accepted_brazil_location
import json


def test_city_requires_its_confirmed_state_and_accepts_iso_bra():
    pattern = brazil_location_pattern("Olinda", "Pernambuco")
    assert pattern.search("Olinda, Pernambuco, BRA")
    assert pattern.search("Olinda, Pernambuco, Brazil")
    assert not pattern.search("Olinda, São Paulo, BRA")
    assert not pattern.search("Olinda, HI, USA")


def test_location_enrichment_does_not_invalidate_correct_accepted_address():
    address = {'name': 'Olinda, Pernambuco, BRA', 'address': {
        'city': 'Olinda', 'region': {'code': 'Pernambuco'}, 'country': {'code': 'BR'}}}
    assert accepted_brazil_location(json.dumps(address), 'Olinda', 'Pernambuco')
    address['address']['CountryCode'] = 'BR'
    address['address']['country']['description'] = ''
    assert accepted_brazil_location(json.dumps(address), 'Olinda', 'Pernambuco')
    assert not accepted_brazil_location(json.dumps(address), 'Olinda', 'São Paulo')
    address['address']['country']['code'] = 'US'
    assert not accepted_brazil_location(json.dumps(address), 'Olinda', 'Pernambuco')
    assert not accepted_brazil_location('', 'Olinda', 'Pernambuco')
