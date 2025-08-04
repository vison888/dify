from core.app.apps.base_app_queue_manager import AppQueueManager, PublishFrom
from core.app.apps.exc import GenerateTaskStoppedError
from core.app.entities.app_invoke_entities import InvokeFrom
from core.app.entities.queue_entities import (
    AppQueueEvent,
    QueueErrorEvent,
    QueueMessageEndEvent,
    QueueStopEvent,
    QueueWorkflowFailedEvent,
    QueueWorkflowPartialSuccessEvent,
    QueueWorkflowSucceededEvent,
    WorkflowQueueMessage,
)


class WorkflowAppQueueManager(AppQueueManager):
    """
    工作流应用队列管理器
    
    专门用于处理工作流应用的事件队列管理。继承自AppQueueManager，
    添加了工作流特有的消息处理逻辑和状态管理。
    
    主要功能：
    1. 管理工作流执行过程中的事件队列
    2. 处理工作流特有的消息类型
    3. 控制事件发布和监听的生命周期
    4. 在特定事件发生时停止队列监听
    """
    
    def __init__(self, task_id: str, user_id: str, invoke_from: InvokeFrom, app_mode: str) -> None:
        """
        初始化工作流应用队列管理器
        
        Args:
            task_id: 任务唯一标识符
            user_id: 用户ID
            invoke_from: 调用来源枚举
            app_mode: 应用模式字符串
        """
        # 调用父类构造函数
        super().__init__(task_id, user_id, invoke_from)
        # 保存应用模式，用于消息标识
        self._app_mode = app_mode

    def _publish(self, event: AppQueueEvent, pub_from: PublishFrom) -> None:
        """
        发布事件到队列
        
        将应用队列事件包装成工作流队列消息并发布到队列中。
        对于特定的终止性事件，会自动停止队列监听。
        
        Args:
            event: 要发布的应用队列事件
            pub_from: 事件发布来源
            
        Raises:
            GenerateTaskStoppedError: 当任务已停止但应用管理器仍尝试发布事件时
        """
        # 创建工作流队列消息，包含任务ID、应用模式和事件
        message = WorkflowQueueMessage(task_id=self._task_id, app_mode=self._app_mode, event=event)

        # 将消息放入队列
        self._q.put(message)

        # 检查是否为终止性事件，如果是则停止监听
        if isinstance(
            event,
            QueueStopEvent                      # 停止事件
            | QueueErrorEvent                   # 错误事件
            | QueueMessageEndEvent              # 消息结束事件
            | QueueWorkflowSucceededEvent       # 工作流成功事件
            | QueueWorkflowFailedEvent          # 工作流失败事件
            | QueueWorkflowPartialSuccessEvent, # 工作流部分成功事件
        ):
            # 停止队列监听
            self.stop_listen()

        # 如果事件来自应用管理器且队列已停止，抛出任务停止错误
        if pub_from == PublishFrom.APPLICATION_MANAGER and self._is_stopped():
            raise GenerateTaskStoppedError()
