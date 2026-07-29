"""CRM push is idempotent: the same email always upserts to the same contact id,
mirroring HubSpot's email-keyed upsert semantics."""
from app.crm.adapter import CRMContact, MockHubSpotAdapter, get_crm_adapter


def test_mock_upsert_is_deterministic_by_email():
    crm = MockHubSpotAdapter()
    c1 = CRMContact(email="Buyer@Acme.com", name="Pat", company="Acme", score=80, notes="")
    c2 = CRMContact(email="buyer@acme.com", name="Pat Buyer", company="Acme", score=90, notes="x")
    # Case-insensitive, content-independent: retries converge on one contact.
    assert crm.upsert_contact(c1) == crm.upsert_contact(c2)


def test_factory_falls_back_to_mock_without_token():
    assert isinstance(get_crm_adapter("hubspot", ""), MockHubSpotAdapter)
    assert isinstance(get_crm_adapter("mock", "irrelevant"), MockHubSpotAdapter)
