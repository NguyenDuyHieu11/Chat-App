import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from chat.conversation_messages_cache import ConversationMessagesCache
from chat.repository_layer.message_repo import MessageRepository
from chat.models import Conversation
from feed.models import AppUser

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for chat functionality.
    
    Uses TWO separate Redis systems:
    1. Channel Layer (Redis DB 0) - for real-time message delivery between consumers
    2. Django Cache (Redis DB 1) - for data persistence via cache strategies
    
    Error Handling:
    - Connection errors: Logged and gracefully reject connection
    - Redis errors: Fallback to database queries
    - Message errors: Logged, user notified via WebSocket
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Initialize cache strategies (uses Redis DB 1)
        self.msg_cache = ConversationMessagesCache()
        self.conversation_id = None
        self.conversation_group_name = None
        self.user = None
        self.user_id = None
        self.user_name = None
        self.app_user = None

    async def connect(self):
        """
        Handle WebSocket connection.
        
        Flow:
        1. Extract conversation ID from URL
        2. Authenticate user
        3. Join channel layer group (Redis DB 0)
        4. Accept connection
        5. Load cached messages (Redis DB 1)
        6. Send history to client
        7. Notify other users
        """
        try:
            # Extract conversation ID from URL
            self.conversation_id = self.scope['url_route']['kwargs'].get('conversation_id')
            
            if not self.conversation_id:
                logger.error("No conversation_id provided in URL")
                await self.close(code=4000)
                return
            
            # Create group name
            self.conversation_group_name = f"chat_{self.conversation_id}"
            
            # Get user information
            self.user = self.scope.get('user')
            
            # Check if user is authenticated
            if not self.user or not self.user.is_authenticated:
                logger.warning(f"Unauthenticated user tried to connect to conversation {self.conversation_id}")
                await self.close(code=4001)
                return
            
            self.user_id = self.user.id
            self.user_name = getattr(self.user, 'username', 'Unknown')
            
            # Validate conversation access
            has_access = await self._validate_conversation_access(self.conversation_id, self.user_id)
            if not has_access:
                logger.warning(
                    f"User {self.user_name} ({self.user_id}) tried to access "
                    f"conversation {self.conversation_id} without permission"
                )
                await self.close(code=4003)
                return
            
            # Join channel layer group (Redis DB 0)
            await self.channel_layer.group_add(
                self.conversation_group_name,
                self.channel_name
            )
            
            logger.info(f"User {self.user_name} ({self.user_id}) joined conversation {self.conversation_id}")

            # Accept WebSocket connection
            await self.accept()

            # Load messages from redis db 1 and send to client
            try:
                messages = await self._get_conversation_messages(self.conversation_id)
                await self.send(text_data=json.dumps({
                    'type': 'message_history',
                    'messages': messages
                }))
            except Exception as e:
                logger.error(f"Error loading messages for conversation {self.conversation_id}: {e}")
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Failed to load message history'
                }))
            
        except Exception as e:
            logger.error(f"Error in connect: {e}", exc_info=True)
            await self.close(code=4002)

    async def disconnect(self, code):
        """
        Handle WebSocket disconnection.
        
        Flow:
        1. Notify other users
        2. Leave channel layer group
        """
        try:
            if self.conversation_group_name:
                # Leave channel layer group
                await self.channel_layer.group_discard(
                    self.conversation_group_name,
                    self.channel_name
                )
                
                logger.info(f"User {self.user_name} ({self.user_id}) left conversation {self.conversation_id}")
                
        except Exception as e:
            logger.error(f"Error in disconnect: {e}", exc_info=True)

    async def receive(self, text_data=None, bytes_data=None):
        """
        Handle incoming WebSocket messages.
        
        Expected message format:
        {
            "type": "chat.message",
            "message": "Hello world",
            "content": "Hello world"  # alias for message
        }
        """
        try:
            if not text_data:
                logger.warning("Received empty message")
                return
            
            # Parse JSON
            try:
                data = json.loads(text_data)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON received: {e}")
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Invalid message format'
                }))
                return
            
            data_type = data.get('type')
            message_content = data.get('message') or data.get('content')
            
            # Validate message content
            if not message_content:
                logger.warning("Received message without content")
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Message content is required'
                }))
                return
            
            # Strip whitespace
            message_content = message_content.strip()
            
            # Check if empty after stripping
            if not message_content:
                logger.warning("Received message with only whitespace")
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Message cannot be empty or contain only whitespace'
                }))
                return
            
            # Check message length (max 5000 characters)
            MAX_MESSAGE_LENGTH = 5000
            if len(message_content) > MAX_MESSAGE_LENGTH:
                logger.warning(f"Received message exceeding max length: {len(message_content)} chars")
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'Message too long. Maximum {MAX_MESSAGE_LENGTH} characters allowed.'
                }))
                return
            
            if data_type == 'chat.message':
                # Save message to database and cache
                try:
                    message_data = await self._save_message(message_content)
                    
                    # Broadcast message to all users in the conversation
                    await self.channel_layer.group_send(
                        self.conversation_group_name,
                        {
                            'type': 'chat_message',
                            'message': message_data
                        }
                    )
                except Exception as e:
                    logger.error(f"Error saving message: {e}", exc_info=True)
                    await self.send(text_data=json.dumps({
                        'type': 'error',
                        'message': 'Failed to send message'
                    }))
            else:
                logger.warning(f"Unknown message type: {data_type}")
                
        except Exception as e:
            logger.error(f"Error in receive: {e}", exc_info=True)
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'An error occurred processing your message'
            }))

    async def chat_message(self, event):
        """
        Handler for chat.message events from channel layer.
        Sends message to WebSocket client.
        """
        try:
            message = event.get('message', {})
            
            # Send message to WebSocket
            await self.send(text_data=json.dumps({
                'type': 'chat.message',
                'message': message
            }))
        except Exception as e:
            logger.error(f"Error in chat_message handler: {e}", exc_info=True)
        
    @database_sync_to_async
    def _validate_conversation_access(self, conversation_id: int, user_id: int) -> bool:
        """
        Validate that the user has access to the conversation.
        
        Returns:
            True if conversation exists and user is a participant, False otherwise
        """
        try:
            conversation = Conversation.objects.filter(id=conversation_id).first()
            if not conversation:
                logger.error(f"Conversation {conversation_id} does not exist")
                return False
            
            # Get AppUser via OneToOne relationship with Django User
            try:
                app_user = self.user.app_user
            except AppUser.DoesNotExist:
                logger.error(f"AppUser not found for user {self.user.username}")
                return False
            
            # Store app_user for later use
            self.app_user = app_user
            
            # Check if user is a participant
            is_participant = conversation.participants.filter(id=app_user.id).exists()
            if not is_participant:
                logger.warning(f"User {self.user_name} is not a participant in conversation {conversation_id}")
                return False
            
            return True
        except Exception as e:
            logger.error(f"Error validating conversation access: {e}", exc_info=True)
            return False
    
    @database_sync_to_async
    def _get_conversation_messages(self, conversation_id: int):
        """Get messages from cache or database (async wrapper)."""
        try:
            return self.msg_cache.get_conversation_messages(conversation_id)
        except Exception as e:
            logger.error(f"Cache error, falling back to database: {e}")
            # Fallback to direct database query
            messages = MessageRepository.get_conversation_messages(conversation_id, limit=50)
            return MessageRepository.serialize_message(messages)
    
    @database_sync_to_async
    def _save_message(self, content: str):
        """Save message to database and cache."""
        try:
            if not self.app_user:
                raise ValueError("AppUser not initialized. Connection may not be properly established.")
            
            # Create persist function for write-through cache
            def persist_func(conversation_id, author_id, content):
                return MessageRepository.create_message(conversation_id, author_id, content)
            
            # Save message using write-through cache strategy
            message_data = {
                'author': self.app_user.id,  # Use AppUser ID, not Django User ID
                'author_name': self.app_user.profile_name,  # Use profile_name from AppUser
                'content': content,
            }
            
            # Use write-through to save to both cache and DB
            success = self.msg_cache.add_message(
                self.conversation_id, 
                message_data, 
                persist_func
            )
            
            if not success:
                raise Exception("Failed to save message to database or cache")
            
            return message_data
            
        except Exception as e:
            logger.error(f"Error saving message: {e}", exc_info=True)
            raise
