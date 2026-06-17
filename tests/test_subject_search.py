from gumptionchain.models import OutflowDAO
from gumptionchain.payload import encode_subject


def test_outflow_populates_plaintext_columns_for_a_stake(app):
    with app.app_context():
        enc = encode_subject('Tabs > Spaces')
        row = OutflowDAO('txid1', 0, 100, support=enc)
        assert row.subject_plain == 'Tabs > Spaces'
        assert row.subject_lower == 'tabs > spaces'


def test_outflow_plaintext_columns_none_for_non_stake(app):
    with app.app_context():
        row = OutflowDAO('txid2', 0, 100, address='GCwhoeverGC')
        assert row.subject_plain is None
        assert row.subject_lower is None
