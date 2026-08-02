from ible.collectors.arxiv import ArxivClient


def test_parse_total_results():
    atom = '''<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>1234</opensearch:totalResults>
    </feed>'''
    assert ArxivClient.parse_total_results(atom) == 1234
