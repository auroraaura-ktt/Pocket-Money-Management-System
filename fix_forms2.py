import pathlib

p = pathlib.Path(r"d:\K's Essential Projects\Pocket Money Management System - 2 - Copy\budget\forms.py")
s = p.read_text(encoding="utf-8")

old_fields = '        fields = ["name", "balance_amount", "estimated_amount"]\n'
new_fields = '        fields = ["name", "balance_amount"]\n'
if old_fields in s:
    s = s.replace(old_fields, new_fields)
else:
    print("fields not found")

old_est = '''    estimated_amount = forms.DecimalField(
        label="Estimated Money (MMK)",
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=0,
        widget=forms.NumberInput(
            attrs={"class": INPUT_CLASS, "min": 0, "step": 1, "placeholder": "Optional"}
        ),
    )
'''
if old_est in s:
    s = s.replace(old_est, "")
else:
    print("estimated block not found")

old_save = '        instance.estimated_amount = self.cleaned_data.get("estimated_amount")\n'
if old_save in s:
    s = s.replace(old_save, "")
else:
    print("save line not found")

p.write_text(s, encoding="utf-8")
print("done")
