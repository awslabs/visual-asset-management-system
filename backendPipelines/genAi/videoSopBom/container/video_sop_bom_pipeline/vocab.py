# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Vocabulary of the Video SOP/BOM Extraction pipeline.

One module holds every controlled value: the 66-column LCA BOM header, the part types, the material
table with its manufacturing techniques, the component-specific drop-down lists and the template
config enums. The JSON Schemas under `schemas/` carry the same values as enums
and the unit tests assert the two agree, so the prompt list the model sees, the schema that validates
its answer and the CSV the renderer writes cannot drift apart.

Values are trimmed and ASCII. The source spreadsheets carry trailing spaces on 24 material names and
one PCB type, and cp1252 bytes in the lab-summary sample; none of that is reproduced here. Spellings
that look like typos ("Assembly- FATP-Electronics", "Protective Pading - Component", "Bio - PU") are
the downstream drop-down values and are kept verbatim.
"""

import unicodedata

# --- LCA BOM contract ------------------------------------------------------------------------------

LCA_BOM_COLUMNS = (
    "part_level",
    "part_type",
    "lab_part_number",
    "manufacturer_part_number",
    "alternative",
    "part_description",
    "qty",
    "material_or_component_type",
    "material_notes",
    "material_composition",
    "material_percent_recycled_content",
    "mass_g_per_unit",
    "manufacturing_country",
    "supplier_carbon_footprint_kgCO2e_per_unit",
    "defect_loss",
    "method_for_weight",
    "primary_manufacturing_process",
    "primary_mfg_yield_loss",
    "primary_mfg_ref_unit",
    "primary_mfg_elect_consumption_kwh_per_unit",
    "primary_elect_from_RE_purchased_percent",
    "primary_RE_wind_percent",
    "primary_RE_solar_percent",
    "primary_elect_from_RE_onsite_percent",
    "primary_mfg_natural_gas_consumption_kwh_per_unit",
    "primary_mfg_f_gas_ghg_emissions_kgCO2e_per_unit",
    "primary_mfg_other_direct_ghg_emissions_kgCO2e_per_unit",
    "primary_mfg_water_consumption_m3",
    "secondary_manufacturing_process",
    "secondary_mfg_yield_loss",
    "secondary_mfg_ref_unit",
    "secondary_mfg_elect_consumption_kwh_per_unit",
    "secondary_elect_from_RE_purchased_percent",
    "secondary_RE_wind_percent",
    "secondary_RE_solar_percent",
    "secondary_elect_from_RE_onsite_percent",
    "secondary_mfg_natural_gas_consumption_kwh_per_unit",
    "secondary_mfg_f_gas_ghg_emissions_kgCO2e_per_unit",
    "secondary_mfg_other_direct_ghg_emissions_kgCO2e_per_unit",
    "secondary_mfg_water_consumption_m3",
    "ic_type",
    "ic_process_node_primary",
    "ic_process_node_secondary",
    "ic_die_size_mm2",
    "ic_mfg_abatement",
    "ic_package_type",
    "ic_package_length_mm",
    "ic_package_width_mm",
    "ic_package_depth_mm",
    "ic_yield_loss",
    "ic_frontend_elect_consumption_kwh_per_unit",
    "ic_backend_elect_consumption_kwh_per_unit",
    "ic_f_gas_per_unit_kg_CO2e",
    "ic_other_direct_ghg_per_unit_kg_CO2e",
    "pcb_board_area_cm2",
    "pcb_type",
    "pcb_layers",
    "pcb_shipping_panel_utilization",
    "pcb_material_utilization",
    "pcb_board_finish",
    "pcb_elect_consumption_kwh_per_unit",
    "display_type",
    "display_active_area_cm2",
    "display_elect_consumption_kwh_per_unit",
    "battery_type",
    "battery_capacity_wh",
)

# The eight columns Instruction.csv marks "MLP Required on ForeSIE = Y".
LCA_BOM_REQUIRED_COLUMNS = LCA_BOM_COLUMNS[:8]

# First line of bom.csv: header row only, UTF-8 without BOM, LF.
LCA_BOM_HEADER_LINE = ",".join(LCA_BOM_COLUMNS) + "\n"
LCA_BOM_HEADER_BYTES = LCA_BOM_HEADER_LINE.encode("utf-8")

# The readable bom.md table, as the reference UI exported it.
BOM_MD_COLUMNS = ("Part Level", "Part Type", "Description", "Qty", "Material", "Mass (g)", "Manufacturing Process")

# --- part types (part_types.csv, 30 rows, file order) ----------------------------------------------

PART_TYPE_DESCRIPTIONS = {
    "Camera or Light Sensing Device": "Component or module that detects and processes light signals including cameras, optical sensors, and photo detectors",
    "Chemicals": "Compound or substance that has been purified or prepared",
    "Protective Materials": "Compounds and materials used for sealing, protecting, or insulating components such as encapsulants, potting compounds, and protective coatings",
    "Connector": "Coupling device used to join electrical terminals to create an electrical circuit",
    "Enclosure": "External housing or casing that contains and protects internal components",
    "FPC": "Flexible printed circuit",
    "Product Assembly Accessory": "Fully assembled accessory",
    "Product Assembly Device": "Fully assembled device",
    "Packaged Product Assembly": "Fully assembled device in its packaging",
    "Cable": "Generic cable, cord or wire",
    "Electronics": "Generic electronic devices integrated into accessories or devices: speakers & microphones",
    "IC": "Integrated circuit (IC), also semiconductor, is an electronic device made up of multiple interconnected electronic components such as transistors, resistors, and capacitors",
    "Mid-Frame": "Internal structural component that provides support and mounting points between front and back portions of a device",
    "Soft Goods": "Less durable goods such as textile, accessory, apparel, clothing, and bedding",
    "Packaging": "Materials and components used to contain, protect, and transport products",
    "Threaded Fasteners": "Mechanical component that uses a spiraling ramp edged out of a cylindrical shaft to join together materials",
    "Mechanical Hardware": "Non-threaded mechanical components such as pins, clips, springs, and structural elements",
    "Display": "Output device for visual presentation of information",
    "Packout Assembly": "Packaged Product Assembly with all of its packaging components ready for shipping/sale",
    "ODM Part": "Part from original design manufacturer",
    "Label": "Piece of material affixed to a product with written or printed information",
    "Frame": "Primary structural component that forms the basic outline and support structure of a device",
    "PCB": "Printed circuit board",
    "PCBA": "Printed circuit board assembly",
    "Print Materials": "Printed materials such as user guides, warranty cards, info cards, and other informational materials",
    "Passive Electrical Device": "Resistor, Capacitor, Inductor, Diode, Transformer, Fuse",
    "Sub-Assembly": "Smaller components that go into the final assembly",
    "Electromechanical Device": "Switches, Buttons",
    "Raw Material": "Unprocessed or minimally processed materials used as inputs for manufacturing processes",
    "Battery Assembly": "Fully assembled battery accessory or device",
}
PART_TYPES = tuple(PART_TYPE_DESCRIPTIONS)

# --- material table (material_or_component_types.csv, 132 rows, file order) -------------------------
# (material_or_component_type, primary manufacturing technique, secondary technique or "")

MATERIAL_TECHNIQUES = (
    ("Battery - Assembly", "Assembly- FATP-Electronics", ""),
    ("Camera Module - Assembly", "Assembly- FATP-Electronics", ""),
    ("Connector - Assembly", "Assembly - General", ""),
    ("Display - Assembly", "Assembly - FATP - Touch & Display", ""),
    ("Enclosure - Assembly", "Molding - Plastics", ""),
    ("FPC - Assembly", "R2R Circuit Processing", ""),
    ("Frame - Assembly", "Molding - Plastics", ""),
    ("Hinge - Assembly", "Forming - Metalwork", ""),
    ("Microphone - Assembly", "Assembly - General", ""),
    ("Optical Module - Assembly", "Assembly- FATP-Electronics", ""),
    ("Packaging - Assembly", "Assembly - General", ""),
    ("Packaging Primary - Assembly", "Assembly - General", ""),
    ("Packaging Secondary - Assembly", "Assembly - General", ""),
    ("PCBA - Assembly", "SMT", ""),
    ("Power Supply Unit - Assembly", "Assembly- FATP-Electronics", ""),
    ("Product Assembly Accessory", "Assembly - FATP", ""),
    ("Packaged Product Assembly", "Assembly - FATP", ""),
    ("Product Assembly Device", "Assembly - FATP", ""),
    ("Speaker - Assembly", "Assembly - General", ""),
    ("Thermal Management- Assembly", "Assembly - General", ""),
    ("Touch Panel - Assembly", "Assembly - FATP - Touch & Display", ""),
    ("Actuator - Component", "Assembly - Electro-Mechanical", ""),
    ("Antenna - Component", "Assembly - Electrical Component", ""),
    ("Battery - Component", "Electrode Coating and Winding", ""),
    ("Bracket Mounting - Component", "Forming - Metalwork", ""),
    ("Button - Component", "Assembly - Electromechanical Component", ""),
    ("Cable - Component", "Cable Processing", ""),
    ("Cable Power - Component", "Cable Processing", ""),
    ("Cable USB - Component", "Cable Processing", ""),
    ("Capacitor - Component", "Passive Electronics Processing", ""),
    ("Connector - Component", "Assembly - Electrical Component", ""),
    ("Connector Power - Component", "Assembly - Electrical Component", ""),
    ("Connector USB - Component", "Assembly - Electrical Component", ""),
    ("Contact Pad - Component", "Thin-Film Deposition", ""),
    ("Cover Lens - Component", "Molding - Plastics", ""),
    ("EMI Shield - Component", "Forming - Metalwork", ""),
    ("Fan - Component", "Assembly - Electromechanical Component", ""),
    ("Ferrite Core - Component", "Passive Electronics Processing", ""),
    ("Filter RF - Component", "Passive Electronics Processing", ""),
    ("FPC - Component", "R2R Circuit Processing", ""),
    ("Fuse - Component", "Passive Electronics Processing", ""),
    ("Gasket - Component", "Molding - Plastics", ""),
    ("Glass Front - Component", "Glass Making Process", ""),
    ("Glass Touch - Component", "Thin-Film Deposition", ""),
    ("Heat Pipe - Component", "Forming - Metalwork", ""),
    ("Heat Sink - Component", "Forming - Metalwork", ""),
    ("Housing Back - Component", "Molding - Plastics", ""),
    ("Housing Front - Component", "Molding - Plastics", ""),
    ("IC Memory - Component", "Semiconductor Device Fabrication", ""),
    ("IC Power - Component", "Semiconductor Device Fabrication", ""),
    ("IC Processor - Component", "Semiconductor Device Fabrication", ""),
    ("Inductor - Component", "Passive Electronics Processing", ""),
    ("LED - Component", "Semiconductor Device Fabrication", ""),
    ("LED Housing - Component", "Molding - Plastics", ""),
    ("LED Optical Stack -Component", "Optical Film Processing", ""),
    ("LED Panel - Component", "Semiconductor Device Fabrication", ""),
    ("Mid-Frame - Component", "Molding - Plastics", ""),
    ("Padding - Component", "Molding - Plastics", ""),
    ("Protective Pading - Component", "Assembly - General", ""),
    ("Resistor - Component", "Passive Electronics Processing", ""),
    ("Thermal Pad - Component", "Chemical Mixing", ""),
    ("Alloys - Aluminum", "Forming - Metalwork", "Smelting"),
    ("Alloys - Copper", "Forming - Metalwork", "Smelting"),
    ("Alloys - Nickel", "Forming - Metalwork", "Smelting"),
    ("Alloys - Other", "Forming - Metalwork", "Smelting"),
    ("Aluminum", "Forming - Metalwork", "Smelting"),
    ("Ceramic", "Forming - Ceramics", ""),
    ("Copper", "Forming - Metalwork", "Smelting"),
    ("Copper Alloys", "Forming - Metalwork", "Smelting"),
    ("Nickel", "Forming - Metalwork", "Smelting"),
    ("REE Magnetic Material", "Chemical", "Smelting"),
    ("Steel", "Forming - Metalwork", "Smelting"),
    ("Stainless Steel", "Forming - Metalwork", "Smelting"),
    ("Titanium", "Forming - Metalwork", "Smelting"),
    ("Zinc", "Forming - Metalwork", "Smelting"),
    ("Glass", "Glass Making Process", "Smelting"),
    ("Bio-PU", "Polymer Synthesis - Bio-Based Polymers", ""),
    ("Cellulose", "Biomass Processing", ""),
    ("Cellulose Acetate", "Biomass Processing", ""),
    ("Cork", "Biomass Processing", ""),
    ("Corrugated Paper", "Corrugator", ""),
    ("Desserto", "Tanning", ""),
    ("Fibers - Cellulose", "Natural Fiber Textile Processing", ""),
    ("Fibers - Cotton", "Natural Fiber Textile Processing", ""),
    ("Fibers - Hemp", "Natural Fiber Textile Processing", ""),
    ("Fibers - Linen", "Natural Fiber Textile Processing", ""),
    ("Fibers - Wool", "Natural Fiber Textile Processing", ""),
    ("Hardwood", "Woodworking", ""),
    ("Hazelnut SOC", "Biomass Processing", ""),
    ("Leather - Cork", "Tanning", ""),
    ("Leather - Cow", "Tanning", ""),
    ("Leather - Other", "Tanning", ""),
    ("Leather - Vegan", "Molding - Plastics", "Polymer Synthesis - Bio-Based Polymers"),
    ("Mars SOC", "Biomass Processing", ""),
    ("Paper", "Papermaking", ""),
    ("Pinatex", "Natural Fiber Textile Processing", ""),
    ("Rice Hulls", "Biomass Processing", ""),
    ("Softwood", "Woodworking", ""),
    ("Starch", "Biomass Processing", ""),
    ("SVT2180 Braskem", "Molding - Plastics", "Polymer Synthesis - Bio-Based Polymers"),
    ("Trifilon", "Molding - Plastics", "Polymer Synthesis - Bio-Based Polymers"),
    ("UPM", "Molding - Plastics", "Polymer Synthesis - Bio-Based Polymers"),
    ("Wood pulp", "Papermaking", ""),
    ("ABS", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("Bio - PU", "Molding - Plastics", "Polymer Synthesis - Bio-Based Polymers"),
    ("EPDM", "Molding - Plastics", "Polymer Synthesis - Thermoset"),
    ("EVA", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("FEP", "Molding - Plastics", "Polymer Synthesis - Fluoropolymer"),
    ("HDPE", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("HIPS", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("LDPE", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("NBR", "Molding - Plastics", "Polymer Synthesis - Thermoset"),
    ("PA", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("PC", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("PC GF20", "Molding - Plastics", "Polymer Compounding"),
    ("PC GF30", "Molding - Plastics", "Polymer Compounding"),
    ("PC/ABS", "Molding - Plastics", "Polymer Compounding"),
    ("PDMS", "Molding - Plastics", "Polymer Synthesis - Thermoset"),
    ("PE", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("PEEK", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("PET", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("Plastic", "Molding - Plastics", "Polymer Compounding"),
    ("PMMA", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("Polystyrene", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("POM", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("PP", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("PTFE", "Molding - Plastics", "Polymer Synthesis - Fluoropolymer"),
    ("PU", "Molding - Plastics", "Polymer Synthesis - Thermoset"),
    ("PU Foam", "Molding - Plastics", "Polymer Foaming"),
    ("PVAc", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("TPE", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
    ("TPU", "Molding - Plastics", "Polymer Synthesis - Thermoplastic"),
)

MATERIAL_TYPES = tuple(row[0] for row in MATERIAL_TECHNIQUES)
MATERIAL_PRIMARY_TECHNIQUE = {row[0]: row[1] for row in MATERIAL_TECHNIQUES}
MATERIAL_SECONDARY_TECHNIQUE = {row[0]: row[2] for row in MATERIAL_TECHNIQUES if row[2]}


def _distinct_in_order(values):
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return tuple(seen)


# Vocabulary (b): the material table's technique columns (28 primary, 7 secondary), which the verified
# example BOM uses. manufacturing_processes.csv (62 program codenames) is deliberately not used.
PRIMARY_TECHNIQUES = _distinct_in_order(row[1] for row in MATERIAL_TECHNIQUES)
SECONDARY_TECHNIQUES = _distinct_in_order(row[2] for row in MATERIAL_TECHNIQUES)

# --- component-specific drop-down lists (component_specific_lists.csv) -----------------------------

YES_NO = ("Yes", "No")
METHOD_FOR_WEIGHT = ("Measured", "Derived from CAD")
PCB_TYPES = ("Flex", "Rigid")
PCB_BOARD_FINISHES = ("ENIG", "OSP")
DISPLAY_TYPES = ("LCD-INCELL", "LCD-OUTCELL", "LCD-TV", "LCD", "EPD", "OLED")
BATTERY_TYPES = (
    "Battery-Alkaline", "Battery-LCO", "Battery-LiPo", "Battery-NCA", "Battery-NCM(811)",
    "Battery-LCO:NCM", "Battery-LCO Recycled Co", "Battery-LCO Recycled Li+Co",
)
IC_TYPES = ("Logic", "DRAM", "NAND")
IC_PACKAGE_TYPES = (
    "BGA", "DFN", "DIP", "Flip Chip", "PLCC", "QFN", "QFP", "SO", "SSOP", "TQFP", "TSOP", "TSSOP", "WLP CSP",
)
# Logic nodes, then the DRAM-only and NAND-only values (N45 and N130 appear in more than one column).
IC_PROCESS_NODE_PRIMARY = (
    "A10", "A14", "N2", "N3", "N5", "N7", "N10", "N14", "N20", "N28", "N45", "N65", "N90", "N130", "N250",
    "1a", "1x", "1y", "1z", "N57",
    "32L", "48L", "64L", "96L", "128L", "176L", "238L", "288L", "352L",
)
IC_PROCESS_NODE_SECONDARY = ("EUV", "EUV_HPC", "HPC", "None", "1D_CoA", "2D_CuA", "2D_CoA", "3D_CuA")

# --- SOP field enums (IdealSteps.md) ----------------------------------------------------------------

FORCE_AMOUNTS = ("light", "moderate", "firm")

# --- lab summary (Lab Summary.csv section structure) -----------------------------------------------

LAB_SUMMARY_SECTIONS = (
    ("1.1", "Background"),
    ("1.2", "Materials Characterization Methodology"),
    ("1.3", "Safety Considerations"),
    ("1.4", "Product information"),
    ("1.5", "Existing BOM"),
    ("1.6", "Verified BOM Data Summary"),
    ("1.7", "Comparative Analysis: Supplier BOM vs Verified BOM"),
    ("1.8", "Primary Manufacturing Processes"),
    ("1.9", "Key Observations"),
)
LAB_SUMMARY_DEFAULT_SAFETY = (
    "No safety procedures or precautions were needed for this teardown other than standardized "
    "laboratory best practices."
)
LAB_SUMMARY_DEFAULT_EXISTING_BOM = "No BOM was provided by the supplier for this request."
LAB_SUMMARY_DEFAULT_COMPARATIVE = "No BOM was provided so comparative analysis could not be performed."

# --- template configuration enums and ceilings ------------------------------------------------------

# `auto` selects Amazon Transcribe language identification; every other value is a batch-supported
# LanguageCode (Amazon Transcribe "Supported languages" table, "Data input" = batch, streaming).
LANGUAGE_CODES = (
    "auto", "en-US", "en-GB", "en-AU", "de-DE", "fr-FR", "es-US", "es-ES", "it-IT", "pt-BR",
    "ja-JP", "ko-KR", "zh-CN",
)
DEFAULT_LANGUAGE_CODE = "en-US"
MODES = ("full", "transcript")
VIDEO_ORDERS = ("selection", "filename")
PART_LEVEL_BASES = ("0", "1")
MAX_KEY_FRAMES_CEILING = 200
ADDITIONAL_INSTRUCTIONS_MAX_CHARS = 4000
TAG_STRING_MAX_CHARS = 256

# --- normalisation ----------------------------------------------------------------------------------

_ASCII_REPLACEMENTS = (
    ("\u2013", "-"), ("\u2014", "-"), ("\u2018", "'"), ("\u2019", "'"),
    ("\u201c", '"'), ("\u201d", '"'), ("\u2026", "..."),
)


def normalize_vocab_value(text):
    """Trim, collapse internal whitespace and reduce a value to ASCII.

    NFKC folds compatibility characters (a no-break space becomes a space); typographic dashes and
    quotes become their ASCII forms; any byte still outside ASCII (mojibake from a cp1252 export) is
    dropped. `None` normalises to the empty string so callers can feed optional model fields directly.
    """
    if text is None:
        return ""
    value = unicodedata.normalize("NFKC", str(text))
    for source, target in _ASCII_REPLACEMENTS:
        value = value.replace(source, target)
    value = value.encode("ascii", "ignore").decode("ascii")
    return " ".join(value.split())


def _index(values):
    return {normalize_vocab_value(value).lower(): value for value in values}


_PART_TYPE_INDEX = _index(PART_TYPES)
_MATERIAL_INDEX = _index(MATERIAL_TYPES)


def match_part_type(text):
    """The canonical part type for `text` (case-insensitive, trimmed), or None when out of vocabulary."""
    return _PART_TYPE_INDEX.get(normalize_vocab_value(text).lower())


def match_material_type(text):
    """The canonical material_or_component_type for `text`, or None when out of vocabulary."""
    return _MATERIAL_INDEX.get(normalize_vocab_value(text).lower())


def primary_technique_for_material(material):
    """The material table's primary manufacturing technique for a canonical material, or None."""
    canonical = match_material_type(material)
    return MATERIAL_PRIMARY_TECHNIQUE.get(canonical) if canonical else None


def secondary_technique_for_material(material):
    """The material table's secondary technique for a canonical material, or None when it has none."""
    canonical = match_material_type(material)
    return MATERIAL_SECONDARY_TECHNIQUE.get(canonical) if canonical else None
