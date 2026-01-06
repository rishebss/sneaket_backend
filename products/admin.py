from django.contrib import admin
from .models import Sneaker
from django.utils.html import format_html

@admin.register(Sneaker)
class SneakerAdmin(admin.ModelAdmin):
    list_display = [
        'name', 
        'brand', 
        'category', 
        'price', 
        'copies', 
        'image_preview',  # This method must exist below
        'is_active',
        'created_at'
    ]
    
    list_filter = ['brand', 'category', 'is_active', 'created_at']
    search_fields = ['name', 'description', 'brand']
    list_editable = ['price', 'copies', 'is_active']
    readonly_fields = ['image_preview', 'created_at', 'updated_at']
    list_per_page = 20
    
    fieldsets = (
        ('Product Information', {
            'fields': ('name', 'brand', 'category', 'description', 'short_description')
        }),
        ('Pricing & Stock', {
            'fields': ('price', 'original_price', 'copies', 'sizes', 'available_sizes')
        }),
        ('Images', {
            'fields': ('img1', 'img2', 'img3', 'image_preview'),
            'description': 'Images will be uploaded to Cloudinary automatically'
        }),
        ('Features & Ratings', {
            'fields': ('features', 'rating', 'review_count')
        }),
        ('Status', {
            'fields': ('is_active', 'created_at', 'updated_at')
        }),
    )
    
    # ADD THIS METHOD - you're missing it!
    def image_preview(self, obj):
        """Show image thumbnail in admin list"""
        if obj.img1:
            return format_html(
                '<img src="{}" style="max-height: 50px; max-width: 50px; border-radius: 4px;" />', 
                obj.img1.url
            )
        return "No Image"
    
    # Add this line to give the column a proper name
    image_preview.short_description = 'Preview'