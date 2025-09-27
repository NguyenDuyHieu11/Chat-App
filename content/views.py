from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from django.db.models import Q, F
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from .models import (
    UserProfile, Follow, Post, Like, Comment, CommentLike,
    Feed, Hashtag, Mention
)
from .serializers import (
    UserSerializer, UserProfileSerializer, FollowSerializer,
    PostSerializer, PostCreateSerializer, LikeSerializer,
    CommentSerializer, FeedSerializer, HashtagSerializer,
    UserRegistrationSerializer, UserUpdateSerializer,
    PasswordChangeSerializer
)
from .permissions import IsAuthorOrReadOnly, IsOwnerOrReadOnly


class StandardResultsSetPagination(PageNumberPagination):
    """Standard pagination for API results"""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class UserViewSet(viewsets.ModelViewSet):
    """ViewSet for User management"""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    pagination_class = StandardResultsSetPagination

    def get_permissions(self):
        if self.action == 'create':
            permission_classes = [permissions.AllowAny]
        elif self.action in ['update', 'partial_update', 'destroy']:
            permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]
        else:
            permission_classes = [permissions.IsAuthenticated]
        return [permission() for permission in permission_classes]

    def get_serializer_class(self):
        if self.action == 'create':
            return UserRegistrationSerializer
        elif self.action in ['update', 'partial_update']:
            return UserUpdateSerializer
        return UserSerializer

    @action(detail=False, methods=['get'])
    def me(self, request):
        """Get current user's profile"""
        serializer = UserUpdateSerializer(request.user, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def follow(self, request, pk=None):
        """Follow a user"""
        user_to_follow = self.get_object()
        
        if user_to_follow == request.user:
            return Response(
                {'error': 'You cannot follow yourself'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        follow, created = Follow.objects.get_or_create(
            follower=request.user,
            following=user_to_follow
        )

        if created:
            return Response({'message': 'Successfully followed user'})
        else:
            return Response(
                {'message': 'Already following this user'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def unfollow(self, request, pk=None):
        """Unfollow a user"""
        user_to_unfollow = self.get_object()
        
        try:
            follow = Follow.objects.get(
                follower=request.user,
                following=user_to_unfollow
            )
            follow.delete()
            return Response({'message': 'Successfully unfollowed user'})
        except Follow.DoesNotExist:
            return Response(
                {'error': 'You are not following this user'}, 
                status=status.HTTP_400_BAD_REQUEST
            )


class PostViewSet(viewsets.ModelViewSet):
    """ViewSet for Post management"""
    queryset = Post.objects.filter(is_deleted=False)
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticated, IsAuthorOrReadOnly]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return Post.objects.filter(is_deleted=False).select_related('author')

    def get_serializer_class(self):
        if self.action == 'create':
            return PostCreateSerializer
        return PostSerializer

    @action(detail=True, methods=['post'])
    def like(self, request, pk=None):
        """Like a post"""
        post = self.get_object()
        like, created = Like.objects.get_or_create(user=request.user, post=post)
        
        if created:
            post.likes_count = F('likes_count') + 1
            post.save(update_fields=['likes_count'])
            return Response({'message': 'Post liked'})
        else:
            return Response(
                {'message': 'Post already liked'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def unlike(self, request, pk=None):
        """Unlike a post"""
        post = self.get_object()
        try:
            like = Like.objects.get(user=request.user, post=post)
            like.delete()
            post.likes_count = F('likes_count') - 1
            post.save(update_fields=['likes_count'])
            return Response({'message': 'Post unliked'})
        except Like.DoesNotExist:
            return Response(
                {'error': 'Post not liked'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=False, methods=['get'])
    def feed(self, request):
        """Get user's personalized feed"""
        following_users = Follow.objects.filter(
            follower=request.user
        ).values_list('following', flat=True)
        
        posts = self.get_queryset().filter(
            Q(author__in=following_users) | Q(author=request.user)
        ).order_by('-created_at')
        
        page = self.paginate_queryset(posts)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(posts, many=True)
        return Response(serializer.data)


class CommentViewSet(viewsets.ModelViewSet):
    """ViewSet for Comment management"""
    queryset = Comment.objects.filter(is_deleted=False)
    serializer_class = CommentSerializer
    permission_classes = [permissions.IsAuthenticated, IsAuthorOrReadOnly]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return Comment.objects.filter(is_deleted=False).select_related('author', 'post')

    def perform_create(self, serializer):
        comment = serializer.save(author=self.request.user)
        # Update post comment count
        comment.post.comments_count = F('comments_count') + 1
        comment.post.save(update_fields=['comments_count'])

    @action(detail=True, methods=['post'])
    def like(self, request, pk=None):
        """Like a comment"""
        comment = self.get_object()
        like, created = CommentLike.objects.get_or_create(
            user=request.user, 
            comment=comment
        )
        
        if created:
            comment.likes_count = F('likes_count') + 1
            comment.save(update_fields=['likes_count'])
            return Response({'message': 'Comment liked'})
        else:
            return Response(
                {'message': 'Comment already liked'}, 
                status=status.HTTP_400_BAD_REQUEST
            )