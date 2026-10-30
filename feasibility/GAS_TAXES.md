# Gas costs and tax scenarios

The gas model separates commodity, regulated variable charges, fixed charges,
progressive national excise, regional surcharge and VAT. Annual progressive
brackets do not reset each month. Actual prior calendar-year consumption is
required to check the bracket applicable to an invoice interval.

The compatibility policy is a versioned modeling assumption, not a statement of
current legal eligibility. New profiles select a supported fiscal rule revision
or explicit parameters and confirm applicability. A region name in a rule table
is policy metadata, never evidence of household location. Gas-bill inputs,
thermal energy and observed invoice comparisons come from the selected profile;
there is no built-in household invoice or annual consumption reference.

Cooking, domestic hot water and space heating remain separate. A disconnected
gas service has different fixed-cost consequences from a retained zero-volume
connection. Missing gas quantities or separated end-use evidence block relevant
conversions rather than inventing a household gas history.

Tests use invented volumes and explicit fiscal parameters. The separate private
compatibility check preserves the externally supplied historical reference.
