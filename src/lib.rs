#[derive(Debug, Default)]
pub struct MozJsContext {
    heap_size_mb: usize,
    active: bool,
}

impl MozJsContext {
    pub fn new(heap_size_mb: usize) -> Self {
        Self { heap_size_mb, active: true }
    }
    pub fn is_active(&self) -> bool { self.active }
    pub fn heap_size(&self) -> usize { self.heap_size_mb }
    pub fn terminate(&mut self) { self.active = false; }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_mozjs_context_lifecycle_aaa() {
        // Arrange
        let mut ctx = MozJsContext::new(128);
        assert!(ctx.is_active());
        assert_eq!(ctx.heap_size(), 128);
        // Act
        ctx.terminate();
        // Assert
        assert!(!ctx.is_active());
    }
}
