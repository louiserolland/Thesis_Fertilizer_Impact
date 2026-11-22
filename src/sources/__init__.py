"""
Registry of all sources: name -> function returning a SourceResult.
To add a new source: write sources/<name>.py with an extract() function and
add one line here.
"""
from sources import comtrade, fao, fred, indices, manual, noaa, portwatch, worldbank

SOURCES = {
    "fao": fao.extract,
    "worldbank": worldbank.extract,
    "fred": fred.extract,
    "noaa": noaa.extract,
    "gpr": indices.extract_gpr,
    "gscpi": indices.extract_gscpi,
    "portwatch": portwatch.extract,
    "comtrade": comtrade.extract,
    "manual": manual.extract,
}
