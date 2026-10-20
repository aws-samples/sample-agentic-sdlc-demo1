# AnyCompany Bookstore - Requirements

## Functional Requirements

### FR-1: User Management
- Users can register with email and password
- Users can sign in/sign out
- Users can manage their profile

### FR-2: Book Catalog
- Display books with title, author, price, and cover image
- Search books by title or author
- Filter books by category

### FR-3: Shopping Cart
- Add/remove books from cart
- Update quantities
- View cart summary with totals

### FR-4: Checkout
- Process orders with shipping information
- Calculate shipping costs
- Send order confirmation emails

## Non-Functional Requirements

### NFR-1: Performance
- Page load time < 2 seconds
- API response time < 500ms

### NFR-2: Scalability
- Support 10,000 concurrent users
- Auto-scale based on demand

### NFR-3: Security
- HTTPS everywhere
- Input validation on all endpoints
- JWT-based authentication
