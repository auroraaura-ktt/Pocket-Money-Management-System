from django.contrib import admin

from budget.models import BudgetCategory, BudgetMonth, DailyExpense, PocketUser

admin.site.register(PocketUser)
admin.site.register(BudgetMonth)
admin.site.register(BudgetCategory)
admin.site.register(DailyExpense)
