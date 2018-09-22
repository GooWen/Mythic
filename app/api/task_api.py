from app import apfell, db_objects
from sanic.response import json
from app.database_models.model import Callback, Operator, Task, Command, FileMeta, Operation
from urllib.parse import unquote_plus
import datetime
from sanic_jwt.decorators import protected, inject_user
from app.api.utils import breakout_quoted_params


# This gets all tasks in the database
@apfell.route(apfell.config['API_BASE'] + "/tasks/", methods=['GET'])
@inject_user()
@protected()
async def get_all_tasks(request, user):
    callbacks = Callback.select()
    operators = Operator.select()
    tasks = Task.select()
    # callbacks_with_operators = await db_objects.prefetch(callbacks, operators)
    full_task_data = await db_objects.prefetch(tasks, callbacks, operators)
    return json([c.to_json() for c in full_task_data])


@apfell.route(apfell.config['API_BASE'] + "/tasks/callback/<cid:int>", methods=['GET'])
@inject_user()
@protected()
async def get_all_tasks_for_callback(request, cid, user):
    try:
        callback = await db_objects.get(Callback, id=cid)
    except Exception as e:
        return json({'status': 'error',
                     'error': 'Callback does not exist'})
    try:
        tasks = Task.select()
        cb_task_data = await db_objects.execute(Task.select().where(Task.callback == callback))
        return json([c.to_json() for c in cb_task_data])
    except Exception as e:
        return json({'status': 'error',
                     'error': 'No Tasks',
                     'msg': str(e)})


# We don't put @protected or @inject_user here since the callback needs to be able to call this function
@apfell.route(apfell.config['API_BASE'] + "/tasks/callback/<cid:int>/nextTask", methods=['GET'])
async def get_next_task(request, cid):
    # gets the next task by time for the callback to do
    try:
        callback = await db_objects.get(Callback, id=cid)
    except Exception as e:
        return json({'status': 'error',
                     'error': 'callback does not exist'})
    try:
        callback.last_checkin = datetime.datetime.now()
        callback.active = True  # always set this to true regardless of what it was before because it's clearly active
        await db_objects.update(callback)  # update the last checkin time
        operation = await db_objects.get(Operation, name=callback.operation.name)
        if not operation.complete:
            tasks = await db_objects.get(Task.select().join(Callback).where(
                (Task.callback == callback) & (Task.status == "submitted")).order_by(Task.timestamp))
        else:
            #  if the operation is done, kill anything that still tries to get tasking
            return json({"command": "exit", "params": ""})
    except Exception as e:
        print(e)
        return json({'command': 'none'})  # return empty if there are no tasks that meet the criteria
    tasks.status = "processing"
    await db_objects.update(tasks)
    return json({"command": tasks.command.cmd, "params": tasks.params, "id": tasks.id})


# create a new task to a specific callback
@apfell.route(apfell.config['API_BASE'] + "/tasks/callback/<cid:int>", methods=['POST'])
@inject_user()
@protected()
async def add_task_to_callback(request, cid, user):
    data = request.json
    data['operator'] = user['username']
    return json(await add_task_to_callback_func(data, cid))


async def add_task_to_callback_func(data, cid):
    try:
        # first see if the operator and callback exists
        op = await db_objects.get(Operator, username=data['operator'])
        cb = await db_objects.get(Callback, id=cid)
        # now check the task and add it if it's valid
        cmd = await db_objects.get(Command, cmd=data['command'])
        file_meta = ""
        # some tasks require a bit more processing, so we'll handle that here so it's easier for the implant
        if cmd.cmd == "upload":
            # we need to get the file into the database before we can signal for the callback to pull it down
            # this will have {path to local file} {path to remote file} in the data['params'] section
            upload_params = await breakout_quoted_params(data['params'])
            file_meta = await db_objects.create(FileMeta, total_chunks=1, chunks_received=1, complete=True,
                                                path=upload_params[0], operation=cb.operation)
            data['params'] = str(file_meta.id) + " " + upload_params[1]

        if cmd.cmd == "download":
            if '"' in data['params']:
                data['params'] = data['params'][1:-1]  # remove "" around the string at this point if they are there
        task = await db_objects.create(Task, callback=cb, operator=op, command=cmd, params=data['params'])
        if cmd.cmd == "upload":
            # now we can associate the task with the filemeta object
            file_meta.task = task
            await db_objects.update(file_meta)
        status = {'status': 'success'}
        task_json = task.to_json()
        return {**status, **task_json}
    except Exception as e:
        print("failed to get something in add_task_to_callback_func " + str(e))
        return {'status': 'error', 'error': 'Failed to create task',  'msg': str(e)}

