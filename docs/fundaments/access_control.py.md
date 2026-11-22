# Secure Role-Based Access Control (RBAC)

### Overview

This module acts as the service layer for managing user permissions and roles. It is a critical component of a secure application, ensuring that users can only access the resources they are authorized for.

The module is a prime example of building a robust logical layer on a solid foundation. It utilizes the secure database connection provided by `postgresql.py` to handle all interactions with the database, guaranteeing that every query is executed safely and correctly without exposing the underlying security logic.

-----

### Core Concepts: The RBAC Model

This module implements a standard Role-Based Access Control model with the following components:

  - **Users:** Application users with unique IDs.
  - **Permissions:** Granular rights or actions a user can perform (e.g., `create_post`, `edit_profile`).
  - **Roles:** Collections of permissions (e.g., `admin`, `editor`, `viewer`).
  - **Assignments:** Roles are assigned to users, granting them all the permissions associated with that role.

-----

### Dependencies

This module is built on your project's existing `fundaments`:

  - `postgresql.py`: The secure database connection module.
  - `asyncpg`: The asynchronous PostgreSQL driver.

-----

### Usage

The `AccessControl` class is designed to be instantiated for a specific user, making it simple to check their permissions.

#### 1\. **Initialization**

The class is initialized with a user's ID.

```python
from fundaments.access_control import AccessControl

# Assume a user with ID 1 exists
user_id = 1
access_control = AccessControl(user_id)
```

#### 2\. **Checking Permissions**

The `has_permission` method checks if the user has a specific permission.

```python
# Check if the user has the 'create_post' permission
can_create_post = await access_control.has_permission('create_post')

if can_create_post:
    print("User is authorized to create a new post.")
else:
    print("Permission denied.")
```

#### 3\. **Retrieving User Information**

You can easily fetch a list of a user's roles or permissions.

```python
# Get all roles assigned to the user
user_roles = await access_control.get_user_roles()
print(f"User's roles: {user_roles}")

# Get all permissions for the user
user_permissions = await access_control.get_user_permissions()
print(f"User's permissions: {user_permissions}")
```

#### 4\. **Administrative Functions**

The module also includes methods for managing roles and permissions (e.g., in an admin panel).

```python
# Create a new role
new_role_id = await access_control.create_role(
    name='moderator',
    description='Manages posts and comments.'
)

# Assign a role to the user
await access_control.assign_role(new_role_id)

# Get permissions for a specific role
moderator_permissions = await access_control.get_role_permissions(new_role_id)
```

-----

### Database Schema (Required)

The module's functionality relies on the following relational schema:

  - `user_roles`: Stores all available roles (`id`, `name`, `description`).
  - `user_permissions`: Stores all available permissions (`id`, `name`, `description`).
  - `user_role_assignments`: A junction table linking `user_id` to `role_id`.
  - `role_permissions`: A junction table linking `role_id` to `permission_id`.

-----

### Security & Architecture

  - **Secure by Design:** This module never executes raw, unsanitized SQL. Every database operation is channeled through the secure `db.execute_secured_query` function, inheriting its protection against SQL injection and other vulnerabilities.
  - **Separation of Concerns:** It successfully separates the business logic of access control from the low-level concerns of database security, making the entire application more robust and easier to maintain.
  - **Extensibility:** New access control methods can be added easily by following the established pattern of using the underlying `db` module.

This `access_control.py` is a prime example of a secure, modular, and extensible building block for your application's architecture.
