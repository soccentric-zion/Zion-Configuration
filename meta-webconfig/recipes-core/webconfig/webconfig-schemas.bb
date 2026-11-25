# webconfig-schemas — schema files for schema-driven rendering (spec §3).
#
# Drops every *.schema.json into /usr/share/webconfig/schemas/. Other recipes
# (customer apps, platform packs) append their own schema files via a
# bbappend to this recipe so adding a setting requires zero UI code (spec §3):
#
#   WEBCONFIG_SCHEMAS_EXTRA:append = " file://my-app.schema.json"
#
# The engine loads this dir at startup; the per-section /api/v1/schemas/:section
# endpoint exposes descriptors to the frontend renderer.

SUMMARY = "SoCcentric WebConfig schema files"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

# Schemas shipped by default. Platform packs add more via bbappend.
SRC_URI = "file://app-example.schema.json file://app-mcu-stm32.schema.json"

# Extra schemas contributed by bbappends (the schema-driven extension point).
WEBCONFIG_SCHEMAS_EXTRA ?= ""
SRC_URI += "${WEBCONFIG_SCHEMAS_EXTRA}"

S = "${WORKDIR}"

do_install() {
    install -d ${D}/usr/share/webconfig/schemas
    for f in ${S}/*.schema.json; do
        [ -e "$f" ] || continue
        install -m 0644 "$f" ${D}/usr/share/webconfig/schemas/
    done
}

FILES:${PN} = "/usr/share/webconfig/schemas"