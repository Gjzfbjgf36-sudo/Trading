# Regulatory and tax considerations (Germany / EU)

**This document is not legal or tax advice, and this system does not attempt to
provide any.** It flags areas that require qualified professional review.
Nothing here is a conclusion.

Automated crypto trading in Germany/EU is not legally or fiscally trivial. The
following areas must be reviewed by qualified professionals **before any
commercial use, before operating the system for or with third parties, and
before deploying it at a scale beyond personal experimentation**.

## Flagged areas

| Area | Why it needs review |
|---|---|
| **Tax reporting** | Treatment of frequent crypto-to-crypto trades, holding periods, fee deductibility, and how arbitrage activity is characterised. |
| **Record keeping** | What records must be retained, in what form, and for how long. The platform is designed to retain complete trade and decision records, but retention *requirements* are a professional question. |
| **Commercial activity (Gewerblichkeit)** | Whether systematic, automated, high-frequency trading constitutes commercial rather than private activity, with the consequences that follow. |
| **Financial-service implications** | Whether any activity — particularly anything performed for third parties, or anything resembling proprietary trading as a service — touches licensing requirements (e.g. under KWG / MiFID II implementations). |
| **MiCA** | Applicability of the EU markets-in-crypto-assets framework to the operator's activities and to the venues used. |
| **Market abuse** | Market-manipulation and abuse rules as they apply to automated trading strategies and order behaviour. |
| **Exchange terms and jurisdiction** | Whether each venue is legally available to the operator, whether automated/API trading is permitted under its terms, and any jurisdiction-specific restrictions. This is an open blocker for the Binance candidate entry in `config/universe.py`. |
| **Sanctions / AML** | Counterparty and address-screening obligations, especially for on-chain activity. |
| **Data protection** | GDPR implications of any personal data processed or logged. |

## Position taken by this project

* Cross-chain strategies remain **paper only**.
* Live trading is gated behind a manual approval that is the operator's, not
  the software's, to grant.
* The system produces complete, immutable decision and execution records
  precisely so that whatever reporting obligations apply can be met.
* **Recommendation: obtain qualified professional review (tax adviser and,
  where third parties or scale are involved, legal counsel) before operating
  this system commercially or for anyone other than yourself.**
