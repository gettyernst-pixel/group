# Financial validation v2 — manual reconciliation

Scenario: the reference restaurant (55 seats, 1.8 turns, 70% utilisation,
$45 net check, 7 days, $350,000 startup investment) **with financing**:
$200,000 loan at 8% over 84 months, so owner equity = $150,000.
Growth set to 0% so hand arithmetic stays flat; the growth path is covered
by unit tests. Every engine value below was produced by the code at doc
generation time — nothing typed by hand.

| Line | By hand | Engine | Match |
|---|---|---|---|
| Monthly covers | 2,102.1 | 2,102.1 | ✓ |
| Net sales | $94,594.50 | $94,594.50 | ✓ |
| COGS (30%) | $28,378.35 | $28,378.35 | ✓ |
| Labor (32%) | $30,270.24 | $30,270.24 | ✓ |
| Contribution margin | $28,378.35 | $28,378.35 | ✓ |
| Fixed cash costs | $20,000.00 | $20,000.00 | ✓ |
| Operating cash flow | $8,378.35 | $8,378.35 | ✓ |
| Debt service (P&I) | $3,117.24 | $3,117.24 | ✓ |
| Owner cash flow | $5,261.11 | $5,261.11 | ✓ |
| Prime cost % | 62.00% | 62.00% | ✓ |
| Project ROI Y1 | 28.73% | 28.73% | ✓ |
| Owner ROI Y1 | 42.09% | 42.09% | ✓ |
| Cumulative owner ROI (5y) | 210.44% | 210.44% | ✓ |
| Cash return multiple | 2.104x | 2.104x | ✓ |
| Operating break-even sales | $66,666.67 | $66,666.67 | ✓ |
| Break-even covers | 1,481.5 | 1,481.5 | ✓ |
| Payback (equity/owner cf) | month 28.5 | month 28.5 | ✓ |
| Sales-to-investment | 3.24x | 3.24x | ✓ |

Definitions: project ROI is pre-financing cash over TOTAL startup
investment; owner ROI is after-debt-service cash over OWNER EQUITY;
cumulative ROI and the return multiple share the equity denominator
(504k/350k = 144% = 1.44x — never 44%); operating break-even is
fixed ÷ contribution-margin ratio and is a different question from
investment payback. All figures pre-tax, cash-based, excluding any
future sale value of the business.