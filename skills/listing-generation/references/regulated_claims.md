# Regulated and High-Risk Claims

Read this reference only when the input contains certification, regulated, safety, filtration-grade, food-contact, test-dependent, medical, child/pet-safety, environmental, an exact alloy/material grade such as 304/316 stainless steel, or comparable evidence claims.

## Group B rule

Keep each risky conclusion out of listing fields until the seller explicitly verifies that the evidence applies to the exact SKU, component, claim scope, marketplace, and category.

High-risk types include:

- certification and regulatory marks
- food-contact/food-grade, BPA-free, non-toxic
- medical, antibacterial, child-safe, pet-safe
- electrical/fire safety, waterproof/leakproof ratings
- load capacity, temperature rating, precision, lifespan
- environmental or sustainability conclusions
- regulated/test-dependent performance
- filter class, filtration efficiency, and allergy suitability
- exact alloy or material grades such as 304/316 stainless steel or 304/316-Edelstahl

Target text alone does not enable Group B. Seller confirmation means user-verified evidence, not model-reviewed evidence.

## Split ordinary facts from risky conclusions

When a phrase contains both, create separate facts:

- ordinary target-supported/Group A fact: generic `Edelstahl`, `Filter-Set`, `Ersatzfilter`, or another concrete product fact
- Group B candidate: `304-Edelstahl`, `316 stainless steel`, `lebensmittelechter Edelstahl`, `HEPA`, `H13`, efficiency percentage, or allergy suitability

Do not let confirmation of one risky claim enable another.

For filtration, keep independent B candidates for:

- HEPA/True HEPA
- H13 or H14
- efficiency percentage and particle size
- allergy suitability

Confirming HEPA never confirms H13/H14, efficiency, or allergy suitability.

## Evidence expectations

- food contact: corresponding material/SKU compliance file
- certification: valid document covering the SKU/component
- waterproof/fire/load/temperature/precision: test method, conditions, scope, and result
- filtration class: SKU/filter-media class report or certification
- filtration efficiency: particle size, airflow, method, scope, and result
- allergy suitability: evidence for the exact suitability wording and marketplace rules
- environmental: evidence for the exact claim scope, not merely a natural material name
- exact alloy/material grade: specification sheet, material certificate, or test report covering the exact SKU and component

Never enable `best`, `universal`, `fits all`, `cure`, or another independently prohibited claim even if the seller asks. Rewrite it as a concrete ordinary fact or omit it.

## Confirmation handling

Use the A/B IDs from the confirmation table the reply refers to, following the
seller follow-up procedure in [core_generation.md](core_generation.md#validation-and-output).
Accept the copyable confirmation form:

```text
A全部确认
A删除：
A修改：
B已核实并启用：
B暂不使用：
```

Enable only explicitly listed B IDs. For `A修改`, use one complete replacement per line: `A修改：A2 = replacement wording`.
