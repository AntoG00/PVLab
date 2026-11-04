# Third-party material and data terms

Reviewed for the source-code-only inventory on 2026-11-04. The PolyForm license
covers original project code only. Dependencies, documentation sources, data,
standards and trademarks retain their own licenses and rights. The release does
not include dependency wheels/native libraries, real GME/PVGIS/ERA5/Google data,
manufacturer catalogues, standards documents or third-party browser libraries.
The browser assets use local application code, CSS and a reviewed synthetic icon.
The performance-map fallback and public fixtures are invented analytic examples,
not republished manufacturer tables or anonymized household traces.

## Dependency compatibility and preservation

Runtime dependencies are installed separately from the hash locks. Their
permissive/PSF licenses allow use by separately licensed application code while
retaining their copyright, conditions and disclaimers. Do not apply PolyForm's
noncommercial restriction to those third-party packages themselves. Do not strip
installed `*.dist-info/licenses`, LICENSE, COPYING or NOTICE files. If distributing
dependency binaries separately, review all bundled-component/runtime notices too;
this source-only distribution does not grant permission to repackage them.

| Runtime/optional package | Recorded license family | Upstream notice |
| --- | --- | --- |
| NumPy 2.5.3 | BSD-3-Clause plus permissive component licenses | [NumPy license](https://numpy.org/doc/stable/license.html) |
| SciPy 1.18.1 | BSD-3-Clause plus bundled component notices | [SciPy license](https://github.com/scipy/scipy/blob/main/LICENSE.txt) |
| Numba 0.68.0 | BSD-2-Clause and third-party notices | [Numba LICENSE](https://github.com/numba/numba/blob/main/LICENSE), LICENSES.third-party in its distribution |
| llvmlite 0.50.0 | BSD-2-Clause; LLVM Apache-2.0 with LLVM exception | [llvmlite LICENSE](https://github.com/numba/llvmlite/blob/main/LICENSE) and installed LLVM notice |
| defusedxml 0.7.1 | Python Software Foundation license | [defusedxml license](https://github.com/tiran/defusedxml/blob/main/LICENSE) |
| tzdata 2026.6 | Apache-2.0; retain included timezone-data notices | [tzdata license](https://github.com/python/tzdata/blob/master/LICENSE) |
| Rasterio 1.5.2, optional legacy tools | BSD-3-Clause; separate GDAL/native-component terms | [Rasterio license](https://github.com/rasterio/rasterio/blob/main/LICENSE.txt) |

The complete development/build locks add MIT, BSD, Apache-2.0 and PSF-licensed
build/test/audit tools. Certifi uses MPL-2.0 for its separate certificate bundle;
its file-level terms do not relicense application code. Packaging is dual
Apache-2.0/BSD-2-Clause; typing-extensions uses PSF terms. The installed metadata
and notice-file inventory for the locked development environment were reviewed;
no copied third-party code/templates were identified in the reviewed application
inventory. Optional Rasterio dependencies retain their individual notices too.
There is no dependency/vendor archive in the export. Full upstream terms govern;
this table is attribution and scope documentation, not a replacement license.

## Provider data and other references

- PVGIS / European Commission JRC and CM SAF SARAH-3 are external data sources.
  [PVGIS documentation](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis_en)
  and [geospatial terms](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/general-information/geospatial-data-download_en)
  describe source acknowledgement; do not treat a geospatial-data statement as a
  blanket license for every API dataset. Retain response provenance, provider,
  dataset/year and applicable attribution when using/exporting downloaded data.
- Historical ERA5 temperature is obtained through Open-Meteo, with underlying
  Copernicus Climate Change Service / ECMWF provenance. [Open-Meteo data licence](https://open-meteo.com/en/licence)
  and [API terms](https://open-meteo.com/en/terms) distinguish CC-BY data attribution
  from free noncommercial API access. Credit Open-Meteo and Copernicus/ECMWF, link
  the applicable data licence and identify changes when sharing provider-derived
  outputs. A project commercial permission does not authorize a provider API plan.
- GME market prices/archives are user-supplied external inputs. [GME terms](https://gme.mercatoelettrico.org/it-it/Home/Pubblicazioni/Indici-GME/PUNIndexGme)
  restrict use and reference separate conditions/disclaimer. Redistribution rights
  for real archives are not cleared and no real archives/data are included.
  Those rights are not needed to redistribute the source-only parser and invented
  fixture generator; users must obtain authorized inputs themselves.
- The standalone optional Google Solar helper is subject to Google account/key,
  attribution and EEA cache restrictions in [the solar notes](feasibility/SOLAR_DATASETS.md).
  No Google responses, keys, imagery or geometry are distributed by this project.
- Vaillant and Stelrad references explain equipment/methodology; no manufacturer
  performance table, catalogue PDF, logo or EN standard is bundled. User-supplied
  manufacturer maps need their own provenance/rights. Product names remain their
  owners' trademarks; references imply no endorsement.

Unresolved rights for downloading/redistributing real provider/manufacturer data
remain limits on those separate activities. They are not claimed approved by the
original-code license. Adding any such material to a future export requires a new
rights/attribution review and all existing privacy and secret scans.
