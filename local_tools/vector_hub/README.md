## Why a local skill DB?

As skills increase, loading all of them into every prompt can be wasteful. That's where `secure_vectorizer.py` comes in - it organizes the skills from the `.md` files into a local database called `skills.db`. This database uses a special system called BM25, and sometimes adds extra embeddings for better results. When the hub gets a request, it only loads the most relevant parts, rather than everything at once. There's a main skill that has all the restrictions and is always loaded, which is tagged as `core`. Everything else is loaded only when it's needed.

## Ideas / roadmap (not final)

- Change the name of the database and connect it to the main program.
- It's better to have a separate list for each type of skill, like security or analysis, rather than having one big list that includes everything.
- `core` is loaded using a special method, not through searching, which means required skills don't rely on rankings.
