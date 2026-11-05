# SIEVE

A user-owned notification boundary for Hermes Agent: event classes × escalation tiers (T0 silent digest → T5 siren through Silent+Focus) × quiet hours with breach floors. Context-aware via Device Access (location/calendar, grant-gated); escalates via mobile alarms (push-gated opt-in).

Status: under construction for the Hermes Plugin Hackathon. Disclosures and screenshots land here before the catalog PR.

## Install (once cataloged)

```
hermes plugins install sieve
hermes plugins enable sieve
```

## Dev

```
hermes plugins doctor ~/.hermes/plugins/sieve
hermes plugins validate ~/.hermes/plugins/sieve --install-deps
pytest ~/.hermes/plugins/sieve/tests -v
```

## License

MIT
