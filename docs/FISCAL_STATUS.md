# Fiscal model review status

Status recorded 2026-10-30: the owner reports review by multiple large language
models (LLMs), without independent fiscal-specialist validation. This is not
professional review, certification or confirmation of current Italian law.
Specialist review is not an owner-imposed blocker for this source-code-only
release. Users remain responsible for checking their scenarios before relying
on financial results. Only Italy / Europe/Rome is implemented; the country-policy
interface remains available for future contributors without implying support.

## Sources, dates and applicability

New profiles supply explicit fiscal parameters, sources, `as_of`, `effective_from`
and exclusive `effective_to` dates. The revision digest binds those inputs.
`ItalyFiscalPolicy` checks the dates against modeled operating years; unsupported
future years require an explicit acknowledgement or updated rules. A source URL,
user confirmation or matching digest is provenance, not legal validation.

Historical compatibility metadata in `feasibility/gas_taxes.py` records these
references with a recorded check date of 2026-10-16. That field describes the
existing project metadata; it is not a claim of specialist review or a fresh
verification of the law today:

| Reference | Recorded scope/date | Limitation |
| --- | --- | --- |
| [Gazzetta Ufficiale, publication 27 June 2026](https://www.gazzettaufficiale.it/atto/serie_generale/caricaArticoloDefault/originario?atto.codiceRedazionale=26A03280&atto.dataPubblicazioneGazzetta=2026-06-27&atto.tipoProvvedimento=TESTO+COORDINATO+DEL+DECRETO-LEGGE) | Historical gas-excise reference, former Mezzogiorno territories; recorded check 2026-10-16 | Compatibility assumptions must not silently become national/current defaults. |
| [Regione Campania ARISGAN](https://archivioportale.regione.campania.it/it/printable/addizionale-regionale-all-accisa-sul-gas-naturale-arisgan-uccs) | Regional surcharge; recorded check 2026-10-16 | A regional reference is not proof of a user's location or eligibility. |
| [ARERA gas-bill guide](https://www.arera.it/en/bolletta/guida-alla-lettura-delle-voci-di-spesa-gas), [Senato response 4-00121](https://www.senato.it/show-doc?id=1084673&leg=18&tipodoc=Sindispr) | Historical VAT/bill-component references; recorded check 2026-10-16 | Context and applicability must be checked for the relevant billing year. |
| [GME PUN Index definition](https://gme.mercatoelettrico.org/it-it/Home/Pubblicazioni/Indici-GME/PUNIndexGme) | National market reference; page checked 2026-10-30 | Wholesale prices are not guaranteed export remuneration or retail terms. |

Electric excise/VAT, export proceeds/tax, PMG, fees, investment relief, regional
and municipal parameters in a new profile remain explicit scenario inputs.
This release does not supply a freshly legally validated national or regional
rate table. Eligibility, tax capacity, principal-residence treatment, future
changes and the user's circumstances may change the result. Do not infer a
fiscal region from a wholesale zone or coordinates.

## Model limitations

The model preserves its tested compatibility formulas. It does not model every
contract, settlement, incentive, tariff class or legal exception. Annual tax
brackets, VAT bases, retained/disconnected gas service and separated thermal end
uses require appropriate inputs. Fixed retail is supported; PUN-indexed retail
billing is not. Long-horizon projections are assumptions, not future tax promises.
LLM agreement and exact numerical regression establish neither legal correctness
nor fitness for a customer's assessment. Sources and effective dates must travel
with the selected profile/revision and exported results. Commercial use requires
the separate written permission described in the project notice and does not
itself provide fiscal validation.
