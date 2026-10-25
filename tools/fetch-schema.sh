#!/bin/sh
# Downloads the MusicXML 4.0 schema (W3C) into .cache/musicxml-4.0/, for
# tools/build.py --schema: musicxml.xsd imports xml.xsd and xlink.xsd by URL,
# rewritten here to the local copies.
#
#   tools/fetch-schema.sh
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$root/.cache/musicxml-4.0"
cd "$root/.cache/musicxml-4.0"
for name in musicxml.xsd xml.xsd xlink.xsd; do
  curl -fsSL -o "$name" "https://raw.githubusercontent.com/w3c/musicxml/v4.0/schema/$name"
done
sed -i 's|schemaLocation="http://www.musicxml.org/xsd/\([a-z]*\.xsd\)"|schemaLocation="\1"|' musicxml.xsd
echo "$(pwd)/musicxml.xsd"
