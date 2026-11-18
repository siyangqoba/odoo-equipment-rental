# Changelog

## 17.0.1.1.0 (2026-11-18)

### Added
- **Weekly and monthly rates.** Lines are priced with the cheapest combination of monthly, weekly and daily rates. The breakdown is shown on orders, invoices and the rental agreement.
- **Return inspection wizard.** Record condition, damage charge and notes per item on return. Damage is settled from the deposit first, and only the excess is invoiced. Damaged items go to maintenance automatically.
- **Preventive maintenance.** Service intervals in rental days, a service log (preventive / repair / inspection) with cost and technician, automatic "Service due" flags and activities, and a Maintenance menu.

### Changed
- Invoice lines are now one line per item at the computed (tiered) price, instead of quantity × daily rate.
- The Return button opens the inspection wizard. Calling `action_return()` from code still works and treats all items as returned in good condition.

### Upgrade notes
- Upgrading from 17.0.1.0.0 needs no migration script. Existing lines are re-priced using the daily rate (weekly and monthly rates start at 0).

## 17.0.1.0.0 (2026-10-22)

- First release: equipment catalogue, rental workflow, double-booking protection, late fees, invoicing, overdue cron, rental agreement PDF.
