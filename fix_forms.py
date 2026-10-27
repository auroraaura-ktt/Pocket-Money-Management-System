import pathlib
p=pathlib.Path(r"d:\K's Essential Projects\Pocket Money Management System - 2 - Copy\budget\forms.py")
s=p.read_text(encoding='utf-8')
s=s.replace('        fields = ["name", "balance_amount", "estimated_amount"]','        fields = ["name", "balance_amount"]')
s=s.replace('        instance.estimated_amount = self.cleaned_data.get("estimated_amount")\n','')
old='''    estimated_amount = forms.DecimalField(
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
s=s.replace(old,'')
p.write_text(s,encoding='utf-8')
print("done")
"""