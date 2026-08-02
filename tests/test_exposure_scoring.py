from ible.analytics.exposure_scoring import build_exposure_weighted_signal


def series(mult=1.0):
    return [(f'202{i//4}-{(i%4+1)*3:02d}-28', mult*(100+i*10)) for i in range(12)]


def test_low_exposure_company_is_excluded():
    profiles = {
        'PURE': {'exposure': 0.9, 'confidence': 0.9, 'evidence': 'pure'},
        'NOISE': {'exposure': 0.1, 'confidence': 1.0, 'evidence': 'noise'},
    }
    signal = build_exposure_weighted_signal('x', {'PURE': series(), 'NOISE': series(-10)}, profiles, 0.3)
    assert signal.raw['eligible_company_count'] == 1
    assert 'PURE' in signal.raw['companies']
    assert 'NOISE' not in signal.raw['companies']


def test_concentration_warning_applies():
    profiles = {'A': {'exposure': 0.9, 'confidence': 0.9}, 'B': {'exposure': 0.31, 'confidence': 0.3}}
    signal = build_exposure_weighted_signal('x', {'A': series(), 'B': series()}, profiles, 0.3)
    assert any('집중도' in w for w in signal.warnings)
