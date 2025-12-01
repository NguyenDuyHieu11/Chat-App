from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages
from feed.models import AppUser


def login_view(request):
    """Handle user login."""
    if request.user.is_authenticated:
        return redirect('chat:room', conversation_id=1)  # Redirect to default chat room
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            # Check if user has an AppUser
            try:
                app_user = user.app_user
            except AppUser.DoesNotExist:
                messages.error(request, 'Your account is not properly set up. Please contact admin.')
                return render(request, 'core/login.html')
            
            login(request, user)
            next_url = request.GET.get('next', 'chat:room')
            if next_url == 'chat:room':
                return redirect('chat:room', conversation_id=1)
            return redirect(next_url)
        else:
            messages.error(request, 'Invalid username or password.')
    
    return render(request, 'core/login.html')


def logout_view(request):
    """Handle user logout."""
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('login')


def register_view(request):
    """Handle user registration."""
    if request.user.is_authenticated:
        return redirect('chat:room', conversation_id=1)
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        password_confirm = request.POST.get('password_confirm')
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        
        # Validation
        if not username or not password:
            messages.error(request, 'Username and password are required.')
            return render(request, 'core/register.html')
        
        if password != password_confirm:
            messages.error(request, 'Passwords do not match.')
            return render(request, 'core/register.html')
        
        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists.')
            return render(request, 'core/register.html')
        
        # Create User and AppUser
        try:
            user = User.objects.create_user(
                username=username,
                password=password,
                first_name=first_name,
                last_name=last_name
            )
            
            # Create linked AppUser
            AppUser.objects.create(
                user=user,
                first_name=first_name or username,
                last_name=last_name,
                profile_name=username
            )
            
            messages.success(request, 'Account created successfully! Please login.')
            return redirect('login')
            
        except Exception as e:
            messages.error(request, f'Error creating account: {str(e)}')
            return render(request, 'core/register.html')
    
    return render(request, 'core/register.html')
