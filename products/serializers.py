from rest_framework import serializers
from .models import Sneaker

class SneakerSerializer(serializers.ModelSerializer):
    img1_url = serializers.SerializerMethodField()
    img2_url = serializers.SerializerMethodField()
    img3_url = serializers.SerializerMethodField()
    
    class Meta:
        model = Sneaker
        fields = '__all__'  # Even simpler!
        read_only_fields = ['created_at', 'updated_at']
    
    def get_img1_url(self, obj):
        return obj.img1.url if obj.img1 else None
    
    def get_img2_url(self, obj):
        return obj.img2.url if obj.img2 else None
    
    def get_img3_url(self, obj):
        return obj.img3.url if obj.img3 else None