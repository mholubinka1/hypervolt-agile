# Issues: bugfix-support-octopus-go

## Schedule charging on the Octopus Go tariff

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5

### What to build

The Octopus client accepts Go tariffs (product code starting `GO-`) alongside Agile (`AGILE-`), and still rejects every other tariff at startup with the tariff code in the error. `get_upcoming_prices` splits each returned rate into back-to-back half-hour Price slots and drops slots that have already ended. Schedule building and everything downstream then work unchanged on Go prices, so charging lands in the cheapest half-hours under the limit, in practice the cheap overnight window.

### Acceptance criteria

- [ ] Given an account whose active agreement is `E-1R-GO-VAR-22-10-14-C`, when the client starts and fetches upcoming prices, then it does not raise and requests rates from the `GO-VAR-22-10-14` product
- [ ] Given Go rates of a 2-hour cheap window followed by a 1-hour standard window, when upcoming prices are fetched, then six half-hour Prices are returned (in any order) covering the three hours: four at the cheap price, then two at the standard price
- [ ] Given a Go window that started 3 hours ago and ends 1 hour after the current half-hour began, when upcoming prices are fetched, then only the current half-hour and the next one are returned
- [ ] Given an Agile account with half-hourly rates, when upcoming prices are fetched, then the same half-hours and prices are returned (in any order)
- [ ] Given a Go window 45 minutes long, when upcoming prices are fetched, then a full half-hour slot and a final 15-minute slot ending at the window's end are returned
- [ ] Given an account on a tariff that is neither Agile nor Go, when the client starts, then it raises an error that names the tariff code

---
