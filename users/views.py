# users/views.py
from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.authtoken.models import Token
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from .models import UserProfile
from .serializers import (
    UserSerializer, 
    UserUpdateSerializer,
    LoginSerializer,
    UserProfileSerializer
)

class RegisterView(APIView):
    """
    View for user registration (sign up)
    URL: POST /api/users/register/
    """
    permission_classes = [permissions.AllowAny]  # Anyone can register
    
    def post(self, request):
        
        # 1. Deserialize incoming data
        serializer = UserSerializer(data=request.data)
        
        # 2. Validate data
        if serializer.is_valid():
            # 3. Create user (serializer.create() is called automatically)
            user = serializer.save()
            
            # 4. Create auth token for API access
            token, created = Token.objects.get_or_create(user=user)
            
            # 5. Return success response
            return Response({
                'message': 'User registered successfully',
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name
                },
                'token': token.key 
            }, status=status.HTTP_201_CREATED)
        
        # 6. Return validation errors if data is invalid
        return Response(
            serializer.errors, 
            status=status.HTTP_400_BAD_REQUEST
        )


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(
                serializer.errors, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user = serializer.validated_data['user']
        login(request, user)
        token, created = Token.objects.get_or_create(user=user)
        
        return Response({
            'message': 'Login successful',
            'user': UserSerializer(user).data,
            'token': token.key
        })

class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        logout(request)
        try:
            request.user.auth_token.delete()
        except:
            pass
        
        return Response({
            'message': 'Logout successful'
        })

class CurrentUserView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        user = request.user
        serializer = UserSerializer(user)
        return Response(serializer.data)

class UpdateProfileView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def put(self, request):
        user = request.user
        serializer = UserUpdateSerializer(
            user, 
            data=request.data, 
            partial=True
        )
        
        if serializer.is_valid():
            serializer.save()
            return Response({
                'message': 'Profile updated successfully',
                'user': UserSerializer(user).data
            })
        
        return Response(
            serializer.errors, 
            status=status.HTTP_400_BAD_REQUEST
        )