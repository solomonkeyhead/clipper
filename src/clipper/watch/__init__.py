"""Campaign watcher: new-campaign emails in a dedicated mailbox -> phone push.

Vyro and Whop both forbid scraping their sites, so the watcher never visits
them. It reads the campaign emails they send, forwarded by the user's own mail
rule into a mailbox used only for this, and pushes the relevant ones to the
user's phone through ntfy.
"""
