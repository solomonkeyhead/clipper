"""Campaign alerts: the Whop feeds the user follows (whop.py) and the judge that reads them (judge.py).

Vyro and Whop both forbid scraping their sites, so nothing here visits them: Whop's own API reads the
feeds the user has joined. The email watcher and ntfy pushes that started this package are gone (D216).
"""
