"""ERP-originated read model: suppliers, purchase orders, lines, inventory, shipments."""
from django.db import models

from .base import TenantOwned


class Supplier(TenantOwned):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ON_HOLD = "ON_HOLD"
        INACTIVE = "INACTIVE"

    class RiskLevel(models.TextChoices):
        LOW = "LOW"
        MEDIUM = "MEDIUM"
        HIGH = "HIGH"
        CRITICAL = "CRITICAL"

    erp_id = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    # Not unique: the seed has deliberate duplicates.
    tax_id = models.CharField(max_length=40, db_index=True, blank=True)
    category = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=2, blank=True)  # ISO-2
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    risk_level = models.CharField(max_length=10, choices=RiskLevel.choices, default=RiskLevel.LOW)
    on_time_delivery_pct = models.DecimalField(max_digits=5, decimal_places=2)
    quality_score = models.DecimalField(max_digits=3, decimal_places=1)  # scale 0-5
    capacity_per_week = models.PositiveIntegerField(null=True, blank=True)  # null = unknown
    contact_email = models.CharField(max_length=254, blank=True)

    class Meta:
        ordering = ["erp_id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "erp_id"], name="uniq_supplier_tenant_erp"),
        ]
        indexes = [models.Index(fields=["tenant", "status"], name="supplier_tenant_status_idx")]

    def __str__(self):
        return f"{self.erp_id} {self.name}"


class PurchaseOrder(TenantOwned):
    class Status(models.TextChoices):
        OPEN = "OPEN"
        RECEIVED = "RECEIVED"
        CANCELLED = "CANCELLED"

    tenant_parent = "supplier"

    erp_id = models.CharField(max_length=40)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    order_date = models.DateField()
    expected_date = models.DateField()  # equals the latest line due_date
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)

    class Meta:
        ordering = ["erp_id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "erp_id"], name="uniq_po_tenant_erp"),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="po_tenant_status_idx"),
            models.Index(fields=["tenant", "supplier"], name="po_tenant_supplier_idx"),
        ]

    def __str__(self):
        return self.erp_id


class PurchaseOrderLine(TenantOwned):
    tenant_parent = "po"

    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    item_id = models.CharField(max_length=40)
    item_name = models.CharField(max_length=200, blank=True)
    qty = models.PositiveIntegerField()
    due_date = models.DateField()
    unit_price = models.DecimalField(max_digits=14, decimal_places=4)

    class Meta:
        ordering = ["po", "line_no"]
        constraints = [
            models.UniqueConstraint(fields=["po", "line_no"], name="uniq_poline_po_lineno"),
        ]
        indexes = [models.Index(fields=["tenant", "item_id"], name="poline_tenant_item_idx")]


class Inventory(TenantOwned):
    item_id = models.CharField(max_length=40)
    item_name = models.CharField(max_length=200, blank=True)
    location_id = models.CharField(max_length=40)
    on_hand = models.IntegerField()
    reserved = models.IntegerField(default=0)
    safety_stock = models.IntegerField(default=0)
    daily_demand = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["item_id", "location_id"]
        verbose_name_plural = "inventory"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "item_id", "location_id"], name="uniq_inventory_tenant_item_loc"
            ),
        ]


class Shipment(TenantOwned):
    class Status(models.TextChoices):
        IN_TRANSIT = "IN_TRANSIT"
        DELIVERED = "DELIVERED"
        CANCELLED = "CANCELLED"

    tenant_parent = "po"

    erp_id = models.CharField(max_length=40)
    po = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="shipments")
    carrier = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.IN_TRANSIT)
    origin = models.CharField(max_length=200, blank=True)
    destination_location_id = models.CharField(max_length=40, blank=True)
    departed_at = models.DateTimeField(null=True, blank=True)
    eta = models.DateField()

    class Meta:
        ordering = ["erp_id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "erp_id"], name="uniq_shipment_tenant_erp"),
        ]

    def __str__(self):
        return self.erp_id
